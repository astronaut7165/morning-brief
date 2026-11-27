"""브리핑 결과를 Obsidian 호환 마크다운으로 vault/raw/[<브리핑 이름>/]YYYY/MM/YYYY-MM-DD.md 에 저장한다."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

import market as mk
from analyzer import KST, VIEW_ARROWS, Analysis, Briefing, Outlook, Pick
from collector import Article, CollectResult

WEEKDAYS = "월화수목금토일"
FOLD_AT_OR_BELOW = 2  # 이 점수 이하 후보는 접어서 보여준다
OUTLOOK_TITLE = "🔮 단기·장기 시선"
OUTLOOK_NOTE = "주어진 시세와 소식을 근거로 한 판단이며, 가격 예측이나 투자 권유가 아닙니다."
_TAG_UNSAFE_RE = re.compile(r"[^\w/-]+", re.UNICODE)


def daily_path(vault_dir: Path, briefing: Briefing) -> Path:
    d = briefing.day
    base = vault_dir / "raw" / briefing.info.name if briefing.info.name else vault_dir / "raw"
    return base / f"{d:%Y}" / f"{d:%m}" / f"{d.isoformat()}.md"


def title_line(briefing: Briefing) -> str:
    d = briefing.day
    return f"{d.isoformat()} ({WEEKDAYS[d.weekday()]}) {briefing.info.title}"


def analysis_heading(briefing: Briefing) -> str:
    info = briefing.info
    return f"{info.analysis_title} — {info.analysis_subtitle}" if info.analysis_subtitle else info.analysis_title


def coin_name(briefing: Briefing, symbol: str) -> str:
    quotes = briefing.market.quotes if briefing.market else []
    return next((f"{q.name} {q.symbol}" for q in quotes if q.symbol == symbol), symbol)


def view_label(view: str, confidence: str = "") -> str:
    label = f"{VIEW_ARROWS.get(view, '')} {view}".strip()
    return f"{label}({confidence})" if confidence else label


# ─────────────────────────────────────────────────────────────────────────────
# 마크다운 조각
# ─────────────────────────────────────────────────────────────────────────────
def _link_text(text: str) -> str:
    return " ".join(text.split()).replace("[", "\\[").replace("]", "\\]")


def _link(text: str, url: str) -> str:
    return f"[{_link_text(text)}]({url.replace(' ', '%20').replace(')', '%29')})"


def _stars(score: int | None) -> str:
    return "☆ 미평가" if score is None else f"{'★' * score}{'☆' * (5 - score)} {score}/5"


def _kst(art: Article) -> str:
    return art.published.astimezone(KST).strftime("%m-%d %H:%M")


def published_label(art: Article) -> str:
    """발행 시각을 '11월 25일 16:55' 형태로 (KST)."""
    t = art.published.astimezone(KST)
    return f"{t.month}월 {t.day}일 {t:%H:%M}"


def refs_label(refs: list[int]) -> str:
    return f"({'·'.join(map(str, refs))}번)" if refs else ""


def source_label(art: Article) -> str:
    publisher = art.extra.get("publisher")
    return f"{art.source} / {publisher}" if publisher and publisher != art.source else art.source


def _tag(text: str) -> str:
    return _TAG_UNSAFE_RE.sub("-", text.strip()).strip("-")


def _render_pick(rank: int, pick: Pick) -> list[str]:
    art = pick.article
    meta = [f"`{_stars(pick.score)}`"]
    if pick.category:
        meta.append(pick.category)
    meta.append(_link(source_label(art), art.url))
    meta.append(f"{published_label(art)} 발행")
    if art.also_in:
        meta.append(f"동시 보도 {len(art.also_in)}곳")
    if art.extra.get("points"):
        meta.append(f"[HN {art.extra['points']}점]({art.extra.get('discussion_url', art.url)})")

    lines = [f"### {rank}. {pick.title}", "", " · ".join(meta), ""]
    lines += [f"- {fact}" for fact in pick.facts]
    links = []
    if pick.title != art.title:
        links.append(f"원문: {_link(art.title, art.url)}")
    related = [(r.source, r.url) for r in pick.related] + list(art.also_in)
    if related:
        links.append("관련 보도: " + ", ".join(_link(s, u) for s, u in related))
    if links:
        lines += ["", "🔗 " + " · ".join(links)]
    lines += [""]
    return lines


def _render_market(b: Briefing) -> list[str]:
    m = b.market
    lines = ["## 💹 시세", ""]
    if not m:
        errors = m.errors if m is not None else []
        return lines + ["시세를 가져오지 못했습니다." + (f" ({', '.join(errors)})" if errors else ""), ""]
    lines += [
        "| 코인 | 가격 | 24시간 | 7일 | 30일 | 1년 | 최고가 대비 | 업비트 · 김프 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for q in m.quotes:
        upbit = f"{mk.krw(q.krw)} · {mk.pct(q.premium, 2)}" if q.krw is not None else "–"
        lines.append(
            f"| {q.name} {q.symbol} | {mk.usd(q.usd)} | {mk.pct(q.change_24h)} | {mk.pct(q.change_7d)} | "
            f"{mk.pct(q.change_30d)} | {mk.pct(q.change_1y)} | {mk.pct(q.ath_change)} | {upbit} |"
        )
    foot = []
    if m.sentiment:
        foot.append(f"공포·탐욕 지수 **{mk.sentiment_label(m.sentiment)}**")
    foot.append(f"기준 {m.fetched_at.astimezone(KST):%m월 %d일 %H:%M}")
    if m.errors:
        foot.append("일부 실패: " + ", ".join(m.errors))
    return lines + ["", " · ".join(foot), ""]


def _render_outlook(b: Briefing, outlook: Outlook) -> list[str]:
    lines = [f"## {OUTLOOK_TITLE}", ""]
    if outlook.mood:
        lines += [f"**지금 분위기** — {outlook.mood}", ""]
    for c in outlook.coins:
        lines += [
            f"### {coin_name(b, c.symbol)} — 단기 {view_label(c.short_view, c.short_confidence)}"
            f" · 장기 {view_label(c.long_view, c.long_confidence)}",
            "",
            f"- **단기(1~4주)** {c.short_reason}",
            f"- **장기(6개월~)** {c.long_reason}",
        ]
        if c.signal:
            lines.append(f"- **판단이 바뀌는 신호** {c.signal}")
        lines.append("")
    return lines + [f"<sub>괄호 안은 판단의 확신도(낮음·보통·높음). {OUTLOOK_NOTE}</sub>", ""]


def _render_analysis(b: Briefing, analysis: Analysis) -> list[str]:
    lines = [f"## {analysis_heading(b)}", ""]
    if analysis.overview:
        lines += [f"**큰 그림** — {analysis.overview}", ""]
    for lens in analysis.lenses:
        ref = refs_label(lens.refs)
        lines += [f"### 👀 {lens.name}" + (f" {ref}" if ref else ""), ""]
        if lens.takeaway:
            lines += [f"**{lens.takeaway}**", ""]
        lines += [lens.insight, ""]
        if lens.question:
            lines += [f"> ❓ {lens.question}", ""]
    return lines


def candidate_groups(briefing: Briefing) -> list[tuple[int | None, list[Article]]]:
    """후보를 점수별로 묶는다. 높은 점수부터, 미평가는 맨 뒤, 각 묶음은 최신순."""
    buckets: dict[int | None, list[Article]] = {}
    for idx, art in enumerate(briefing.candidates):
        buckets.setdefault(briefing.scores.get(idx), []).append(art)
    order = sorted((k for k in buckets if k is not None), reverse=True)
    if None in buckets:
        order.append(None)
    return [(s, sorted(buckets[s], key=lambda a: a.published, reverse=True)) for s in order]


def _render_candidates(briefing: Briefing) -> list[str]:
    picked = {id(p.article) for p in briefing.picks}
    lines = [f"## 📋 전체 후보 {len(briefing.candidates)}건", ""]
    for score, arts in candidate_groups(briefing):
        label = "미평가" if score is None else f"{'★' * score} {score}점"
        rows = []
        for art in arts:
            mark = "🔝 " if id(art) in picked else ""
            extra = f" · +{len(art.also_in)}곳" if art.also_in else ""
            rows.append(f"- {mark}{_link(art.title, art.url)} — {source_label(art)} · {_kst(art)}{extra}")
        if score is not None and score <= FOLD_AT_OR_BELOW:
            # Obsidian 에서 접힌 상태로 보이는 callout
            lines += [f"> [!note]- {label} ({len(rows)}) — 펼쳐 보기"] + [f"> {r}" for r in rows] + [""]
        else:
            lines += [f"### {label} ({len(rows)})", ""] + rows + [""]
    return lines


def _render_sources(result: CollectResult) -> list[str]:
    lines = [
        "## 🩺 수집 현황",
        "",
        f"원본 {result.total_fetched}건 → 중복 {result.duplicates}건 · "
        f"이전 브리핑 {result.seen_before}건 제외 → 신규 {len(result.articles)}건",
        "",
    ]
    for stat in result.stats:
        status = f"⚠️ 실패 — {stat.error}" if stat.error else f"{stat.fetched}건"
        lines.append(f"- {stat.name}: {status}")
    lines.append("")
    return lines


def render_markdown(briefing: Briefing, result: CollectResult) -> str:
    categories = sorted({p.category for p in briefing.picks if p.category})
    front = {
        "date": briefing.day.isoformat(),
        "type": f"{briefing.info.name}-briefing" if briefing.info.name else "daily-briefing",
        "engine": briefing.engine,
        "collected": len(result.articles),
        "candidates": len(briefing.candidates),
        "picks": len(briefing.picks),
        "tags": ["briefing"] + ([briefing.info.name] if briefing.info.name else [])
        + [t for t in (_tag(c) for c in categories) if t],
    }
    lines = ["---", yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip(), "---", ""]
    lines += [f"# {briefing.info.emoji} {title_line(briefing)}", ""]

    if briefing.headline:
        lines += [f"> **오늘의 주요 소식** — {briefing.headline}", ""]
    if briefing.error:
        lines += [
            "> [!warning] LLM 분석에 실패해 기본 정렬(동시 보도 수·HN 점수·최신순)로 대신 골랐습니다.",
            f"> `{briefing.error}`",
            "",
        ]

    if briefing.market is not None:
        lines += _render_market(briefing)
    lines += ["## 📰 오늘의 소식", ""]
    if briefing.picks:
        for rank, pick in enumerate(briefing.picks, 1):
            lines += _render_pick(rank, pick)
    else:
        lines += ["오늘은 기준 점수를 넘은 기사가 없습니다.", ""]
    if briefing.outlook:
        lines += ["---", ""] + _render_outlook(briefing, briefing.outlook)
    if briefing.analysis:
        lines += ["---", ""] + _render_analysis(briefing, briefing.analysis)
    lines += ["---", ""]

    if briefing.candidates:
        lines += _render_candidates(briefing)
    lines += _render_sources(result)
    lines += [
        f"<sub>생성: {briefing.generated_at.astimezone(KST):%Y-%m-%d %H:%M} KST · {briefing.engine}</sub>",
        "",
        "<!-- 깊이 볼 만한 항목은 vault/curated/ 로 승격해 내 인사이트를 덧붙이세요. -->",
        "",
    ]
    return "\n".join(lines)


def write_daily(briefing: Briefing, result: CollectResult, vault_dir: Path) -> Path:
    path = daily_path(vault_dir, briefing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(briefing, result), encoding="utf-8")
    return path
