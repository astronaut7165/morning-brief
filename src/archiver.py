"""브리핑 결과를 Obsidian 호환 마크다운으로 vault/raw/YYYY/MM/YYYY-MM-DD.md 에 저장한다."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from analyzer import KST, Briefing, Pick
from collector import Article, CollectResult

WEEKDAYS = "월화수목금토일"
_TAG_UNSAFE_RE = re.compile(r"[^\w/-]+", re.UNICODE)


def daily_path(vault_dir: Path, briefing: Briefing) -> Path:
    d = briefing.day
    return vault_dir / "raw" / f"{d:%Y}" / f"{d:%m}" / f"{d.isoformat()}.md"


def title_line(briefing: Briefing) -> str:
    d = briefing.day
    return f"{d.isoformat()} ({WEEKDAYS[d.weekday()]}) 모닝 브리핑"


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


def _source_label(art: Article) -> str:
    publisher = art.extra.get("publisher")
    return f"{art.source} / {publisher}" if publisher else art.source


def _tag(text: str) -> str:
    return _TAG_UNSAFE_RE.sub("-", text.strip()).strip("-")


def _render_pick(rank: int, pick: Pick) -> list[str]:
    art = pick.article
    meta = [f"`{_stars(pick.score)}`"]
    if pick.category:
        meta.append(pick.category)
    meta.append(_link(_source_label(art), art.url))
    meta.append(_kst(art))
    if art.also_in:
        meta.append(f"동시 보도 {len(art.also_in)}곳")
    if art.extra.get("points"):
        meta.append(f"[HN {art.extra['points']}점]({art.extra.get('discussion_url', art.url)})")

    lines = [f"### {rank}. {pick.title}", "", " · ".join(meta), ""]
    if pick.summary:
        lines += [f"**무슨 일이** — {pick.summary}", ""]
    if pick.opportunity:
        lines += [f"**💡 사업 기회** — {pick.opportunity}", ""]
    if pick.action:
        lines += [f"**🛠 실무 적용** — {pick.action}", ""]
    if pick.title != art.title:
        lines += [f"- 원문 제목: {_link(art.title, art.url)}"]
    related = [(r.source, r.url, r.title) for r in pick.related]
    related += [(s, u, s) for s, u in art.also_in]
    if related:
        lines += ["- 관련 보도: " + ", ".join(_link(f"{s}", u) for s, u, _ in related)]
    lines += ["", "---", ""]
    return lines


def _render_candidates(briefing: Briefing) -> list[str]:
    picked = {id(p.article) for p in briefing.picks}
    buckets: dict[int | None, list[Article]] = {}
    for idx, art in enumerate(briefing.candidates):
        buckets.setdefault(briefing.scores.get(idx), []).append(art)

    lines = [f"## 📋 전체 후보 {len(briefing.candidates)}건", ""]
    order = sorted((k for k in buckets if k is not None), reverse=True)
    if None in buckets:
        order.append(None)
    for score in order:
        label = "미평가" if score is None else f"{'★' * score} {score}점"
        lines += [f"### {label} ({len(buckets[score])})", ""]
        for art in sorted(buckets[score], key=lambda a: a.published, reverse=True):
            mark = "🔝 " if id(art) in picked else ""
            extra = f" · +{len(art.also_in)}곳" if art.also_in else ""
            lines.append(f"- {mark}{_link(art.title, art.url)} — {_source_label(art)} · {_kst(art)}{extra}")
        lines.append("")
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
        "type": "daily-briefing",
        "engine": briefing.engine,
        "collected": len(result.articles),
        "candidates": len(briefing.candidates),
        "picks": len(briefing.picks),
        "tags": ["briefing"] + [t for t in (_tag(c) for c in categories) if t],
    }
    lines = ["---", yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip(), "---", ""]
    lines += [f"# 🌅 {title_line(briefing)}", ""]

    if briefing.headline:
        lines += [f"> **오늘의 한 줄** — {briefing.headline}", ""]
    if briefing.error:
        lines += [
            "> [!warning] LLM 분석에 실패해 기본 정렬(동시 보도 수·HN 점수·최신순)로 대신 골랐습니다.",
            f"> `{briefing.error}`",
            "",
        ]

    lines += ["## 🔝 Top Picks", ""]
    if briefing.picks:
        for rank, pick in enumerate(briefing.picks, 1):
            lines += _render_pick(rank, pick)
    else:
        lines += ["오늘은 기준 점수를 넘은 기사가 없습니다.", ""]

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
