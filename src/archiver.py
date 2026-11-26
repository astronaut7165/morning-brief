"""브리핑 결과를 Obsidian 호환 마크다운으로 vault/raw/YYYY/MM/YYYY-MM-DD.md 에 저장한다."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from analyzer import KST, Analysis, Briefing, Insight, Pick
from collector import Article, CollectResult

WEEKDAYS = "월화수목금토일"
FOLD_AT_OR_BELOW = 2  # 이 점수 이하 후보는 접어서 보여준다
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


def published_label(art: Article) -> str:
    """발행 시각을 '11월 25일 16:55' 형태로 (KST)."""
    t = art.published.astimezone(KST)
    return f"{t.month}월 {t.day}일 {t:%H:%M}"


def refs_label(refs: list[int]) -> str:
    return f"({'·'.join(map(str, refs))}번)" if refs else ""


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


def _render_insights(title: str, items: list[Insight]) -> list[str]:
    if not items:
        return []
    lines = [f"### {title}", ""]
    for it in items:
        ref = refs_label(it.refs)
        lines.append(f"- {it.text}" + (f" {ref}" if ref else ""))
    return lines + [""]


def _render_analysis(analysis: Analysis) -> list[str]:
    lines = ["## 🧭 종합 분석 — 사업 기회·실무 적용 관점", ""]
    if analysis.overview:
        lines += [analysis.overview, ""]
    lines += _render_insights("💡 사업 기회", analysis.opportunities)
    lines += _render_insights("🛠 이번 주 실무 적용", analysis.actions)
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
        rows = []
        for art in sorted(buckets[score], key=lambda a: a.published, reverse=True):
            mark = "🔝 " if id(art) in picked else ""
            extra = f" · +{len(art.also_in)}곳" if art.also_in else ""
            rows.append(f"- {mark}{_link(art.title, art.url)} — {_source_label(art)} · {_kst(art)}{extra}")
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
        lines += [f"> **오늘의 주요 소식** — {briefing.headline}", ""]
    if briefing.error:
        lines += [
            "> [!warning] LLM 분석에 실패해 기본 정렬(동시 보도 수·HN 점수·최신순)로 대신 골랐습니다.",
            f"> `{briefing.error}`",
            "",
        ]

    lines += ["## 📰 오늘의 소식", ""]
    if briefing.picks:
        for rank, pick in enumerate(briefing.picks, 1):
            lines += _render_pick(rank, pick)
    else:
        lines += ["오늘은 기준 점수를 넘은 기사가 없습니다.", ""]
    if briefing.analysis:
        lines += ["---", ""] + _render_analysis(briefing.analysis)
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
