"""메신저 브리핑 발송.

환경변수가 설정된 채널로만 보낸다. 여러 채널을 동시에 켜도 된다.
  Slack     SLACK_WEBHOOK_URL                (Incoming Webhook, Mattermost 호환)
  Discord   DISCORD_WEBHOOK_URL
  Telegram  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
  KakaoTalk KAKAO_REST_API_KEY (+ KAKAO_CLIENT_SECRET) — '나에게 보내기'. 최초 1회 scripts/kakao_auth.py 실행
웹훅 URL과 토큰은 비밀값이므로 오류 메시지·로그에 절대 넣지 않는다.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

import market as mk
from analyzer import VIEW_ARROWS, Briefing, Pick
from archiver import OUTLOOK_TITLE, analysis_heading, coin_name, published_label, refs_label, title_line, view_label

log = logging.getLogger(__name__)

SCORE_COLORS = {5: 0xE8590C, 4: 0xF59F00, 3: 0x4DABF7}  # Discord embed 색상
DEFAULT_COLOR = 0x868E96
ANALYSIS_COLOR = 0x7048E8
OUTLOOK_COLOR = 0x0CA678


class NotifyError(RuntimeError):
    pass


def _post(url: str, label: str, attempts: int = 3, **kwargs) -> requests.Response:
    """비밀 URL·토큰이 예외 메시지에 섞이지 않도록 감싼 POST. 429/5xx 는 재시도."""
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, timeout=20, **kwargs)
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
    return f"{b.info.emoji} {title_line(b)}"


def _quote_line(q: mk.Quote) -> str:
    change = f"{mk.arrow(q.change_24h)}{abs(q.change_24h):.1f}%" if q.change_24h is not None else "–"
    return f"{q.symbol} {mk.usd(q.usd)} {change} (7일 {mk.pct(q.change_7d)})"


def _market_lines(b: Briefing) -> list[str]:
    """시세 요약 줄. 코인 브리핑이 아니면 빈 목록."""
    m = b.market
    if m is None:
        return []
    if not m:
        return ["시세를 가져오지 못했습니다."]
    return [_quote_line(q) for q in m.quotes if q.usd is not None] + _market_extra(b)


def _market_extra(b: Briefing) -> list[str]:
    """공포·탐욕 지수와 김치 프리미엄 한 줄."""
    m = b.market
    extra = []
    if m and m.sentiment:
        extra.append(f"공포·탐욕 {m.sentiment.value} {m.sentiment.label}")
    if m and (q := next((q for q in m.quotes if q.premium is not None), None)):
        extra.append(f"김프 {q.symbol} {mk.pct(q.premium, 2)}")
    return [" · ".join(extra)] if extra else []


def _outlook_parts(b: Briefing, fmt: Callable[[str], str], bold: Callable[[str], str]) -> list[str]:
    o = b.outlook
    parts = [f"{bold('지금 분위기')} {fmt(o.mood)}"] if o.mood else []
    for c in o.coins:
        head = f"{coin_name(b, c.symbol)} — 단기 {view_label(c.short_view, c.short_confidence)} · 장기 {view_label(c.long_view, c.long_confidence)}"
        lines = [bold(fmt(head))]
        lines += [fmt(f"단기: {c.short_reason}"), fmt(f"장기: {c.long_reason}")]
        if c.signal:
            lines.append(fmt(f"🚩 바뀌는 신호: {c.signal}"))
        parts.append("\n".join(lines))
    return parts


def _stats_line(b: Briefing) -> str:
    return f"새 기사 {len(b.candidates)}건 중 {len(b.picks)}건 선별 · {b.engine}"


def _warning(b: Briefing) -> str | None:
    if b.error:
        return "⚠️ LLM 분석 실패로 기본 정렬 결과입니다. 자세한 내용은 실행 로그를 확인하세요."
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
    parts.append(f"{published_label(p.article)} 발행")
    if p.article.also_in:
        parts.append(f"외 {len(p.article.also_in)}곳 보도")
    return " · ".join(parts)


def _analysis_parts(b: Briefing, fmt: Callable[[str], str], bold: Callable[[str], str]) -> list[str]:
    """사업가의 시선을 문단 목록으로. fmt 는 채널별 이스케이프, bold 는 채널별 굵게(이스케이프된 텍스트를 받음)."""
    a = b.analysis
    parts = []
    if a.overview:
        parts.append(f"{bold('큰 그림')} {fmt(a.overview)}")
    for lens in a.lenses:
        ref = refs_label(lens.refs)
        lines = [bold(fmt(f"👀 {lens.name}")) + (f" {ref}" if ref else "")]
        if lens.takeaway:
            lines.append(f"→ {fmt(lens.takeaway)}")
        lines.append(fmt(lens.insight))
        if lens.question:
            lines.append(f"❓ {fmt(lens.question)}")
        parts.append("\n".join(lines))
    return parts


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
    if market := _market_lines(b):
        blocks.append(section(_slack_escape("\n".join(market))))
    blocks.append({"type": "divider"})

    for rank, p in enumerate(b.picks, 1):
        title = _slack_escape(p.title).replace("|", "｜")
        lines = [f"*{rank}. <{_slack_escape(p.article.url)}|{title}>*", f"_{_slack_escape(_meta(p))}_"]
        lines += [f"• {_slack_escape(f)}" for f in p.facts]
        blocks.append(section("\n".join(lines)))
    if not b.picks:
        blocks.append(section("오늘은 기준 점수를 넘은 기사가 없습니다."))
    if b.outlook:
        blocks += [{"type": "divider"}, section(f"*{OUTLOOK_TITLE}*")]
        blocks += [section(part) for part in _outlook_parts(b, _slack_escape, lambda t: f"*{t}*")]
    if b.analysis:
        blocks += [{"type": "divider"}, section(f"*{_slack_escape(analysis_heading(b))}*")]
        blocks += [section(part) for part in _analysis_parts(b, _slack_escape, lambda t: f"*{t}*")]

    footer = _stats_line(b)
    if archive_url:
        footer += f" · <{_slack_escape(archive_url)}|전체 브리핑 보기>"
    blocks += [{"type": "divider"}, context(footer)]
    return {"text": f"{_header(b)} — {b.headline or _stats_line(b)}", "blocks": blocks[:50]}


def send_slack(b: Briefing, archive_url: str | None) -> None:
    _post(os.environ["SLACK_WEBHOOK_URL"], "Slack", json=build_slack_payload(b, archive_url))


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
    content_lines += _market_lines(b)
    if not b.picks:
        content_lines.append("오늘은 기준 점수를 넘은 기사가 없습니다.")

    embeds = []
    for rank, p in enumerate(b.picks, 1):
        embeds.append({
            "title": f"{rank}. {p.title}"[:256],
            "url": p.article.url,
            "description": "\n".join(f"• {f}" for f in p.facts)[:4000],
            "color": SCORE_COLORS.get(p.score or 0, DEFAULT_COLOR),
            "footer": {"text": _meta(p)[:2048]},
        })
    if b.outlook:
        embeds.append({
            "title": OUTLOOK_TITLE,
            "description": "\n\n".join(_outlook_parts(b, str, lambda t: f"**{t}**"))[:4000],
            "color": OUTLOOK_COLOR,
            "footer": {"text": "가격 예측이나 투자 권유가 아닙니다"},
        })
    if b.analysis:
        embeds.append({
            "title": analysis_heading(b)[:256],
            "description": "\n\n".join(_analysis_parts(b, str, lambda t: f"**{t}**"))[:4000],
            "color": ANALYSIS_COLOR,
            "footer": {"text": "위 소식들을 바탕으로 한 분석입니다"},
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
        _post(url, "Discord", json={**message, "allowed_mentions": {"parse": []}})
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
    head += [esc(line) for line in _market_lines(b)]
    blocks = ["\n".join(head)]

    for rank, p in enumerate(b.picks, 1):
        lines = [
            f'<b>{rank}. <a href="{esc(p.article.url, quote=True)}">{esc(p.title)}</a></b>',
            f"<i>{esc(_meta(p))}</i>",
        ]
        lines += [f"• {esc(f)}" for f in p.facts]
        blocks.append("\n".join(lines)[:TELEGRAM_MSG_LIMIT])
    if not b.picks:
        blocks.append("오늘은 기준 점수를 넘은 기사가 없습니다.")
    if b.outlook:
        blocks.append(f"<b>{esc(OUTLOOK_TITLE)}</b>")
        blocks += [part[:TELEGRAM_MSG_LIMIT] for part in _outlook_parts(b, esc, lambda t: f"<b>{t}</b>")]
    if b.analysis:
        blocks.append(f"<b>{esc(analysis_heading(b))}</b>")
        blocks += [part[:TELEGRAM_MSG_LIMIT] for part in _analysis_parts(b, esc, lambda t: f"<b>{t}</b>")]

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
        _post(
            url,
            "Telegram",
            json={
                "chat_id": os.environ["TELEGRAM_CHAT_ID"],
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# KakaoTalk — '나에게 보내기' (내 카카오톡 '나와의 채팅'으로 발송)
#   텍스트 템플릿은 말풍선당 200자 제한이라 '헤더 / 기사별 / 시선별'로 나눠 보낸다.
#   버튼 링크는 앱 설정 [제품 링크 관리 > 웹 도메인]에 등록된 도메인만 열리므로 전체 브리핑(GitHub) 하나로 통일한다.
#   리프레시 토큰은 갱신 때마다 바뀔 수 있어 .env 가 아닌 저장소 밖 파일(KAKAO_TOKEN_FILE)에 보관한다.
# ─────────────────────────────────────────────────────────────────────────────
KAKAO_TEXT_LIMIT = 200
KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
KAKAO_FALLBACK_LINK = "https://github.com"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _pack(required: list[str], optional: list[str], limit: int = KAKAO_TEXT_LIMIT) -> str:
    """required 줄은 항상 넣고(넘치면 자름), optional 줄은 들어가는 만큼만 넣는다."""
    text = _clip("\n".join(required), limit)
    for i, line in enumerate(optional):
        room = limit - len(text) - 1
        if len(line) <= room:
            text += "\n" + line
        elif i == 0 and room >= 40:  # 첫 줄도 안 들어가면 잘라서라도 일부를 보여준다
            text += "\n" + _clip(line, room)
            break
        else:
            break
    return text


def _kakao_coin_line(b: Briefing, q: mk.Quote) -> str:
    change = f"{mk.arrow(q.change_24h)}{abs(q.change_24h):.1f}%" if q.change_24h is not None else "–"
    line = f"{q.symbol} {mk.usd(q.usd)} {change}"
    if view := next((c for c in b.outlook.coins if c.symbol == q.symbol), None):
        line += f" · 단기 {VIEW_ARROWS.get(view.short_view, '→')} 장기 {VIEW_ARROWS.get(view.long_view, '→')}"
    return line


def build_kakao_messages(b: Briefing) -> list[str]:
    """주제별 말풍선 하나. 핵심만 담고, 자세한 내용은 '전체 브리핑 보기'(휴대폰용 페이지)에서 본다."""
    lines = [_header(b)]
    if warning := _warning(b):
        lines.append(warning)
    optional: list[str] = []
    m = b.market
    if m is not None:  # 코인: 코인별 시세와 단기·장기 방향, 심리 지표
        lines.append("")
        if m:
            lines += [_kakao_coin_line(b, q) for q in m.quotes if q.usd is not None] + _market_extra(b)
        else:
            lines.append("시세를 가져오지 못했습니다.")
        if b.headline:
            optional.append(f"\n{b.headline}")
    else:  # 기술·비즈니스: 주요 소식 한 줄 + 들어가는 만큼 기사 제목
        if b.headline:
            lines += ["", b.headline]
        if b.picks:
            optional = [f"{r}. {p.title}" for r, p in enumerate(b.picks, 1)]
            optional[0] = "\n" + optional[0]  # 제목 목록 앞에 빈 줄
        else:
            lines += ["", "오늘은 기준 점수를 넘은 기사가 없습니다."]
    return [_pack(lines, optional)]


def kakao_token_file() -> Path:
    default = Path.home() / ".config" / "morning-brief" / "kakao_token.json"
    return Path(os.getenv("KAKAO_TOKEN_FILE") or default).expanduser()


def _save_kakao_tokens(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _kakao_client() -> dict:
    params = {"client_id": os.environ["KAKAO_REST_API_KEY"]}
    if os.getenv("KAKAO_CLIENT_SECRET"):
        params["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    return params


def _store_kakao_response(data: dict, stored: dict, path: Path) -> None:
    now = datetime.now(timezone.utc)
    if data.get("refresh_token"):  # 만료 1개월 전부터 새 토큰이 오고 이전 토큰은 폐기되므로 반드시 교체
        stored["refresh_token"] = data["refresh_token"]
        if data.get("refresh_token_expires_in"):
            expires = now + timedelta(seconds=int(data["refresh_token_expires_in"]))
            stored["refresh_token_expires_at"] = expires.isoformat(timespec="seconds")
    stored["updated_at"] = now.isoformat(timespec="seconds")
    _save_kakao_tokens(stored, path)


def kakao_exchange_code(code: str, redirect_uri: str) -> Path:
    """인가 코드를 토큰으로 바꿔 저장한다 (scripts/kakao_auth.py 에서 최초 1회)."""
    resp = _post(KAKAO_TOKEN_URL, "Kakao 토큰 발급", data={
        "grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code, **_kakao_client(),
    })
    data = resp.json()
    if "talk_message" not in str(data.get("scope", "talk_message")):
        raise NotifyError("'카카오톡 메시지 전송(talk_message)' 동의가 빠졌습니다. 동의항목 설정을 확인하세요.")
    path = kakao_token_file()
    _store_kakao_response(data, {}, path)
    return path


def kakao_access_token() -> str:
    path = kakao_token_file()
    if not path.exists():
        raise NotifyError("카카오 토큰이 없습니다. scripts/kakao_auth.py 로 한 번 로그인하세요.")
    stored = json.loads(path.read_text(encoding="utf-8"))
    try:
        resp = _post(KAKAO_TOKEN_URL, "Kakao 토큰 갱신", data={
            "grant_type": "refresh_token", "refresh_token": stored["refresh_token"], **_kakao_client(),
        })
    except NotifyError as exc:
        if "KOE322" in str(exc) or "invalid_grant" in str(exc):
            raise NotifyError(f"{exc} — 로그인이 만료됐습니다. scripts/kakao_auth.py 로 다시 로그인하세요.") from None
        raise
    data = resp.json()
    _store_kakao_response(data, stored, path)
    return data["access_token"]


def kakao_send_texts(texts: list[str], link: str, button: str = "전체 브리핑 보기") -> None:
    token = kakao_access_token()
    for text in texts:
        template = {
            "object_type": "text",
            "text": text[:KAKAO_TEXT_LIMIT],
            "link": {"web_url": link, "mobile_web_url": link},
            "button_title": button,
        }
        resp = _post(
            KAKAO_MEMO_URL, "Kakao",
            headers={"Authorization": f"Bearer {token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
        )
        if resp.json().get("result_code") != 0:
            raise NotifyError(f"Kakao 발송 실패: {resp.text[:200]}")
        time.sleep(0.3)


def send_kakao(b: Briefing, archive_url: str | None) -> None:
    kakao_send_texts(build_kakao_messages(b), archive_url or KAKAO_FALLBACK_LINK)


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
    if os.getenv("KAKAO_REST_API_KEY"):
        channels.append(("kakao", send_kakao))
    return channels


def notify(briefing: Briefing, archive_url: str | None = None) -> dict[str, bool]:
    """설정된 모든 채널로 발송하고 채널별 성공 여부를 돌려준다. 한 채널 실패가 다른 채널을 막지 않는다."""
    channels = configured_channels()
    if not channels:
        log.warning("설정된 메신저가 없어 발송을 건너뜁니다 (SLACK_WEBHOOK_URL / DISCORD_WEBHOOK_URL / TELEGRAM_* / KAKAO_*)")
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
