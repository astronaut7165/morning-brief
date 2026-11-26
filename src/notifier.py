"""메신저(웹훅) 브리핑 발송.

환경변수가 설정된 채널로만 보낸다. 여러 채널을 동시에 켜도 된다.
  Slack     SLACK_WEBHOOK_URL                (Incoming Webhook, Mattermost 호환)
  Discord   DISCORD_WEBHOOK_URL
  Telegram  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
웹훅 URL과 토큰은 비밀값이므로 오류 메시지·로그에 절대 넣지 않는다.
"""

from __future__ import annotations

import html
import logging
import os
import time
from typing import Callable

import requests

from analyzer import Briefing, Pick
from archiver import title_line

log = logging.getLogger(__name__)

SCORE_COLORS = {5: 0xE8590C, 4: 0xF59F00, 3: 0x4DABF7}  # Discord embed 색상
DEFAULT_COLOR = 0x868E96


class NotifyError(RuntimeError):
    pass


def _post_json(url: str, payload: dict, label: str, attempts: int = 3) -> requests.Response:
    """비밀 URL이 예외 메시지에 섞이지 않도록 감싼 POST. 429/5xx 는 재시도."""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, json=payload, timeout=20)
        except requests.RequestException as exc:
            if attempt == attempts:
                raise NotifyError(f"{label} 연결 실패 ({type(exc).__name__})") from None
            time.sleep(2 * attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < attempts:
                wait = resp.headers.get("Retry-After")
                time.sleep(min(float(wait), 30) if wait and wait.replace(".", "", 1).isdigit() else 2 * attempt)
                continue
        if resp.status_code >= 400:
            raise NotifyError(f"{label} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp
    raise NotifyError(f"{label} 재시도 한도 초과")


# ─────────────────────────────────────────────────────────────────────────────
# 공통 문구
# ─────────────────────────────────────────────────────────────────────────────
def _header(b: Briefing) -> str:
    return f"🌅 {title_line(b)}"


def _stats_line(b: Briefing) -> str:
    return f"새 기사 {len(b.candidates)}건 중 {len(b.picks)}건 선별 · {b.engine}"


def _warning(b: Briefing) -> str | None:
    if b.error:
        return "⚠️ LLM 분석 실패로 기본 정렬 결과입니다. 자세한 내용은 Actions 로그를 확인하세요."
    return None


def _meta(p: Pick) -> str:
    parts = []
    if p.score is not None:
        parts.append(f"{'★' * p.score} {p.score}/5")
    if p.category:
        parts.append(p.category)
    source = p.article.source
    if p.article.extra.get("publisher"):
        source = p.article.extra["publisher"]
    parts.append(source)
    if p.article.also_in:
        parts.append(f"외 {len(p.article.also_in)}곳 보도")
    return " · ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Slack
# ─────────────────────────────────────────────────────────────────────────────
def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_slack_payload(b: Briefing, archive_url: str | None) -> dict:
    def section(text: str) -> dict:
        return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2990]}}

    def context(text: str) -> dict:
        return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:2990]}]}

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": _header(b)[:150], "emoji": True}}
    ]
    if b.headline:
        blocks.append(section(f"> *{_slack_escape(b.headline)}*"))
    if warning := _warning(b):
        blocks.append(context(warning))
    blocks.append({"type": "divider"})

    for rank, p in enumerate(b.picks, 1):
        title = _slack_escape(p.title).replace("|", "｜")
        lines = [f"*{rank}. <{_slack_escape(p.article.url)}|{title}>*", f"_{_slack_escape(_meta(p))}_"]
        if p.summary:
            lines.append(_slack_escape(p.summary))
        if p.opportunity:
            lines.append(f"💡 *사업 기회* {_slack_escape(p.opportunity)}")
        if p.action:
            lines.append(f"🛠 *실무 적용* {_slack_escape(p.action)}")
        blocks.append(section("\n".join(lines)))
    if not b.picks:
        blocks.append(section("오늘은 기준 점수를 넘은 기사가 없습니다."))

    footer = _stats_line(b)
    if archive_url:
        footer += f" · <{_slack_escape(archive_url)}|전체 브리핑 보기>"
    blocks += [{"type": "divider"}, context(footer)]
    return {"text": f"{_header(b)} — {b.headline or _stats_line(b)}", "blocks": blocks[:50]}


def send_slack(b: Briefing, archive_url: str | None) -> None:
    _post_json(os.environ["SLACK_WEBHOOK_URL"], build_slack_payload(b, archive_url), "Slack")


# ─────────────────────────────────────────────────────────────────────────────
# Discord
# ─────────────────────────────────────────────────────────────────────────────
DISCORD_MSG_LIMIT = 5500  # 메시지당 embed 총 글자 수 한도(6000)에 여유를 둔 값


def build_discord_messages(b: Briefing, archive_url: str | None) -> list[dict]:
    content_lines = [f"## {_header(b)}"]
    if b.headline:
        content_lines.append(f"> **{b.headline}**")
    if warning := _warning(b):
        content_lines.append(warning)
    if not b.picks:
        content_lines.append("오늘은 기준 점수를 넘은 기사가 없습니다.")

    embeds = []
    for rank, p in enumerate(b.picks, 1):
        desc = [p.summary] if p.summary else []
        if p.opportunity:
            desc.append(f"💡 **사업 기회** {p.opportunity}")
        if p.action:
            desc.append(f"🛠 **실무 적용** {p.action}")
        embeds.append({
            "title": f"{rank}. {p.title}"[:256],
            "url": p.article.url,
            "description": "\n\n".join(desc)[:4000],
            "color": SCORE_COLORS.get(p.score or 0, DEFAULT_COLOR),
            "footer": {"text": _meta(p)[:2048]},
        })

    def size(e: dict) -> int:
        return len(e["title"]) + len(e["description"]) + len(e["footer"]["text"])

    messages: list[dict] = [{"content": "\n".join(content_lines)[:2000], "embeds": []}]
    for embed in embeds:
        current = messages[-1]["embeds"]
        if len(current) >= 10 or sum(map(size, current)) + size(embed) > DISCORD_MSG_LIMIT:
            messages.append({"embeds": []})
        messages[-1]["embeds"].append(embed)

    footer = _stats_line(b) + (f" · [전체 브리핑 보기](<{archive_url}>)" if archive_url else "")
    messages.append({"content": footer[:2000]})
    return messages


def send_discord(b: Briefing, archive_url: str | None) -> None:
    url = os.environ["DISCORD_WEBHOOK_URL"]
    for message in build_discord_messages(b, archive_url):
        _post_json(url, {**message, "allowed_mentions": {"parse": []}}, "Discord")
        time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────────────────────────────────────
TELEGRAM_MSG_LIMIT = 4000  # 실제 한도 4096


def build_telegram_messages(b: Briefing, archive_url: str | None) -> list[str]:
    esc = html.escape
    head = [f"<b>{esc(_header(b))}</b>"]
    if b.headline:
        head.append(f"<i>{esc(b.headline)}</i>")
    if warning := _warning(b):
        head.append(esc(warning))
    blocks = ["\n".join(head)]

    for rank, p in enumerate(b.picks, 1):
        lines = [
            f'<b>{rank}. <a href="{esc(p.article.url, quote=True)}">{esc(p.title)}</a></b>',
            f"<i>{esc(_meta(p))}</i>",
        ]
        if p.summary:
            lines.append(esc(p.summary))
        if p.opportunity:
            lines.append(f"💡 <b>사업 기회</b> {esc(p.opportunity)}")
        if p.action:
            lines.append(f"🛠 <b>실무 적용</b> {esc(p.action)}")
        blocks.append("\n".join(lines)[:TELEGRAM_MSG_LIMIT])
    if not b.picks:
        blocks.append("오늘은 기준 점수를 넘은 기사가 없습니다.")

    footer = esc(_stats_line(b))
    if archive_url:
        footer += f' · <a href="{esc(archive_url, quote=True)}">전체 브리핑 보기</a>'
    blocks.append(footer)

    messages: list[str] = []
    for block in blocks:
        if messages and len(messages[-1]) + 2 + len(block) <= TELEGRAM_MSG_LIMIT:
            messages[-1] += "\n\n" + block
        else:
            messages.append(block)
    return messages


def send_telegram(b: Briefing, archive_url: str | None) -> None:
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage"
    for text in build_telegram_messages(b, archive_url):
        _post_json(
            url,
            {
                "chat_id": os.environ["TELEGRAM_CHAT_ID"],
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
            "Telegram",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────────────
def configured_channels() -> list[tuple[str, Callable[[Briefing, str | None], None]]]:
    channels = []
    if os.getenv("SLACK_WEBHOOK_URL"):
        channels.append(("slack", send_slack))
    if os.getenv("DISCORD_WEBHOOK_URL"):
        channels.append(("discord", send_discord))
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        channels.append(("telegram", send_telegram))
    return channels


def notify(briefing: Briefing, archive_url: str | None = None) -> dict[str, bool]:
    """설정된 모든 채널로 발송하고 채널별 성공 여부를 돌려준다. 한 채널 실패가 다른 채널을 막지 않는다."""
    channels = configured_channels()
    if not channels:
        log.warning("설정된 메신저가 없어 발송을 건너뜁니다 (SLACK_WEBHOOK_URL / DISCORD_WEBHOOK_URL / TELEGRAM_*)")
        return {}
    results: dict[str, bool] = {}
    for name, send in channels:
        try:
            send(briefing, archive_url)
            results[name] = True
            log.info("[%s] 발송 완료", name)
        except Exception as exc:
            results[name] = False
            log.error("[%s] 발송 실패 — %s", name, exc if isinstance(exc, NotifyError) else type(exc).__name__)
    return results
