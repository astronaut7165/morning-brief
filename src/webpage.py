"""브리핑을 휴대폰에서 읽기 좋은 HTML 페이지로 만든다.

docs/YYYY/MM/YYYY-MM-DD.html 과 모아보기(docs/index.html)를 쓰고, GitHub Pages 가 docs/ 를 그대로 공개한다.
외부 스크립트·폰트 없이 한 파일로 끝나서 카카오톡 인앱 브라우저에서도 바로 열린다.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from analyzer import KST, Analysis, Briefing, Pick
from archiver import (
    ANALYSIS_SUBTITLE,
    ANALYSIS_TITLE,
    FOLD_AT_OR_BELOW,
    WEEKDAYS,
    candidate_groups,
    published_label,
    source_label,
)
from collector import CollectResult

log = logging.getLogger(__name__)

NEXT_SLOT = "<!--next-->"  # 다음 날 페이지가 생기면 이 자리에 '다음 브리핑' 링크를 끼운다
_DESC_RE = re.compile(r'<meta name="description" content="([^"]*)">')
_GITHUB_RE = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)$")
_PAGE_GLOB = "[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html"


# ─────────────────────────────────────────────────────────────────────────────
# 경로 · 주소
# ─────────────────────────────────────────────────────────────────────────────
def page_path(docs_dir: Path, day: date) -> Path:
    return docs_dir / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}.html"


def pages_base_url(repo_url: str | None) -> str | None:
    """https://github.com/{owner}/{repo} → https://{owner}.github.io/{repo}"""
    match = _GITHUB_RE.match(repo_url or "")
    if not match:
        return None
    owner, repo = match.group(1).lower(), match.group(2)
    host = f"https://{owner}.github.io"
    return host if repo.lower() == f"{owner}.github.io" else f"{host}/{repo}"


def page_url(path: Path, docs_dir: Path, repo_url: str | None) -> str | None:
    base = pages_base_url(repo_url)
    return f"{base}/{quote(path.relative_to(docs_dir).as_posix())}" if base else None


def wait_until_live(url: str, timeout: float = 300, interval: float = 10) -> bool:
    """push 직후 GitHub Pages 배포가 끝나 페이지가 열릴 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            # 캐시된 404 를 피하려고 매번 다른 주소로 확인
            resp = requests.get(url, params={"t": time.time_ns()}, timeout=15)
            if resp.status_code == 200:
                return True
        except requests.RequestException:
            pass
        if time.monotonic() + interval > deadline:
            log.warning("페이지가 %d초 안에 열리지 않아 그대로 발송합니다: %s", timeout, url)
            return False
        time.sleep(interval)


def _pages(docs_dir: Path) -> list[tuple[date, Path]]:
    found = []
    for path in docs_dir.glob(_PAGE_GLOB):
        try:
            found.append((date.fromisoformat(path.stem), path))
        except ValueError:
            continue
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# HTML 조각
# ─────────────────────────────────────────────────────────────────────────────
def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _href(url: str) -> str:
    """피드에서 온 주소는 http(s) 만 링크로 쓴다."""
    return _e(url) if urlsplit(url).scheme in ("http", "https") else "#"


def _a(text: str, url: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f'<a{attr} href="{_href(url)}" rel="noopener">{_e(" ".join(text.split()))}</a>'


def day_label(day: date) -> str:
    return f"{day.month}월 {day.day}일 {WEEKDAYS[day.weekday()]}요일"


def _pick(rank: int, pick: Pick) -> str:
    art = pick.article
    meta = []
    if pick.category:
        meta.append(f'<span class="cat">{_e(pick.category)}</span>')
    meta.append(_e(source_label(art)))
    meta.append(f"{_e(published_label(art))} 발행")
    if art.also_in:
        meta.append(f"동시 보도 {len(art.also_in)}곳")
    if art.extra.get("points"):
        meta.append(_a(f"HN {art.extra['points']}점", art.extra.get("discussion_url") or art.url))

    out = [f'<article class="card pick" id="pick-{rank}">']
    out.append(f'<div class="pick-head"><span class="rank">{rank}</span><h3>{_e(pick.title)}</h3></div>')
    if pick.title != art.title:
        out.append(f'<p class="orig">{_e(art.title)}</p>')
    out.append(f'<p class="meta">{" · ".join(meta)}</p>')
    if pick.facts:
        out.append('<ul class="facts">' + "".join(f"<li>{_e(f)}</li>" for f in pick.facts) + "</ul>")
    stars = ""
    if pick.score is not None:
        stars = f'<span class="stars" title="중요도 {pick.score}/5">{"★" * pick.score}{"☆" * (5 - pick.score)}</span>'
    out.append(f'<div class="actions">{_a("원문 보기", art.url, "btn")}{stars}</div>')
    related = [(r.source, r.url) for r in pick.related] + list(art.also_in)
    if related:
        out.append('<p class="related">관련 보도 · ' + " · ".join(_a(s, u) for s, u in related) + "</p>")
    out.append("</article>")
    return "\n".join(out)


def _analysis(analysis: Analysis) -> str:
    out = ['<section id="lens">', f"<h2>{_e(ANALYSIS_TITLE)}</h2>", f'<p class="sub">{_e(ANALYSIS_SUBTITLE)}</p>']
    if analysis.overview:
        out.append(f'<div class="overview"><p class="label">큰 그림</p><p>{_e(analysis.overview)}</p></div>')
    for lens in analysis.lenses:
        out.append('<article class="card lens">')
        out.append(f"<h3>👀 {_e(lens.name)}</h3>")
        if lens.refs:
            out.append('<p class="refs">' + "".join(f'<a href="#pick-{r}">{r}번 기사</a>' for r in lens.refs) + "</p>")
        if lens.takeaway:
            out.append(f'<p class="takeaway">{_e(lens.takeaway)}</p>')
        out.append(f"<p>{_e(lens.insight)}</p>")
        if lens.question:
            out.append(f'<p class="question">❓ {_e(lens.question)}</p>')
        out.append("</article>")
    out.append("</section>")
    return "\n".join(out)


def _candidates(b: Briefing) -> str:
    picked = {id(p.article) for p in b.picks}
    groups = []
    for score, arts in candidate_groups(b):
        label = "미평가" if score is None else f"{'★' * score} {score}점"
        rows = []
        for art in arts:
            mark = "🔝 " if id(art) in picked else ""
            extra = f" · +{len(art.also_in)}곳" if art.also_in else ""
            rows.append(
                f"<li>{mark}{_a(art.title, art.url)}"
                f"<small>{_e(source_label(art))} · {_e(published_label(art))}{extra}</small></li>"
            )
        is_open = "" if score is None or score <= FOLD_AT_OR_BELOW else " open"
        groups.append(
            f'<details class="group"{is_open}><summary>{label} <span class="count">{len(arts)}</span></summary>'
            f'<ul>{"".join(rows)}</ul></details>'
        )
    return (
        f'<details class="fold"><summary>📋 전체 후보 {len(b.candidates)}건</summary>'
        f'<div class="fold-body">{"".join(groups)}</div></details>'
    )


def _sources(result: CollectResult) -> str:
    rows = []
    for stat in result.stats:
        value = f'<span class="bad">실패 — {_e(stat.error)}</span>' if stat.error else f"{stat.fetched}건"
        rows.append(f"<li><span>{_e(stat.name)}</span><span>{value}</span></li>")
    summary = (
        f"원본 {result.total_fetched}건 → 중복 {result.duplicates}건 · "
        f"이전 브리핑 {result.seen_before}건 제외 → 신규 {len(result.articles)}건"
    )
    return (
        '<details class="fold"><summary>🩺 수집 현황</summary>'
        f'<div class="fold-body"><p class="meta">{summary}</p><ul class="stats">{"".join(rows)}</ul></div></details>'
    )


_CSS = """
:root{--bg:#f5f5f3;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;
--accent:#2563eb;--accent-soft:#eef3ff;--lens:#6d4ae6;--lens-soft:#f3effe;--warn-bg:#fff4e5;--warn:#8a4b00}
@media (prefers-color-scheme:dark){:root{--bg:#0f1012;--card:#1a1b1e;--text:#ececef;--muted:#a1a1aa;--line:#2b2c31;
--accent:#7ea6ff;--accent-soft:#1c2640;--lens:#b39dff;--lens-soft:#241d3a;--warn-bg:#3a2a12;--warn:#ffcf8a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);
font:17px/1.7 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR","Malgun Gothic",sans-serif;
word-break:keep-all;overflow-wrap:anywhere}
.wrap{max-width:680px;margin:0 auto;padding:24px 16px calc(48px + env(safe-area-inset-bottom))}
a{color:var(--accent);text-decoration:none}
h1{font-size:28px;line-height:1.3;margin:2px 0 10px;letter-spacing:-.02em}
h2{font-size:21px;line-height:1.4;margin:40px 0 4px;letter-spacing:-.01em}
h3{font-size:18px;line-height:1.45;margin:0}
p{margin:0}
.eyebrow{color:var(--muted);font-size:14px;font-weight:600}
.headline{font-size:16px;color:var(--muted);margin-bottom:16px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips a{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:14px;color:var(--text)}
.sub{color:var(--muted);font-size:15px;margin-bottom:12px}
.warn{background:var(--warn-bg);color:var(--warn);border-radius:12px;padding:12px 14px;font-size:15px;margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 16px;margin:12px 0;scroll-margin-top:12px}
.card:target{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.pick-head{display:flex;gap:10px;align-items:flex-start}
.rank{flex:none;width:26px;height:26px;margin-top:1px;border-radius:50%;background:var(--accent);color:#fff;
font-size:14px;font-weight:700;line-height:26px;text-align:center}
.orig{color:var(--muted);font-size:14px;line-height:1.5;margin:6px 0 0 36px}
.meta{color:var(--muted);font-size:14px;line-height:1.55;margin-top:8px}
.cat{color:var(--accent);font-weight:600}
.facts{margin:12px 0 0;padding-left:20px}
.facts li{margin:6px 0}
.actions{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px}
.btn{background:var(--accent-soft);color:var(--accent);border-radius:10px;padding:8px 14px;font-size:15px;font-weight:600}
.stars{color:#f5a524;font-size:14px;letter-spacing:1px}
.related{color:var(--muted);font-size:14px;line-height:1.6;margin-top:10px}
.overview{background:var(--lens-soft);border-radius:16px;padding:18px 16px;margin:12px 0}
.label{color:var(--lens);font-size:14px;font-weight:700;margin-bottom:6px}
.refs{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.refs a{background:var(--lens-soft);color:var(--lens);border-radius:999px;padding:2px 10px;font-size:13px}
.takeaway{font-weight:700;margin:12px 0 8px}
.question{margin-top:14px;padding:12px 14px;border-left:3px solid var(--lens);background:var(--lens-soft);
border-radius:0 10px 10px 0;font-size:15px}
details.fold{background:var(--card);border:1px solid var(--line);border-radius:16px;margin:16px 0}
details>summary{cursor:pointer;list-style:none;font-weight:700}
details>summary::-webkit-details-marker{display:none}
details>summary::after{content:"▾";float:right;color:var(--muted);transition:transform .15s}
details:not([open])>summary::after{transform:rotate(-90deg)}
details.fold>summary{padding:16px}
.fold-body{padding:0 16px 12px}
details.group{border-top:1px solid var(--line)}
details.group>summary{padding:12px 0;font-size:15px;color:var(--muted)}
.count{font-weight:400}
.fold ul{list-style:none;padding:0;margin:0}
.group li{padding:10px 0;border-top:1px dashed var(--line);font-size:15px;line-height:1.5}
.group li small{display:block;color:var(--muted);font-size:13px;margin-top:2px}
.stats li{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-top:1px dashed var(--line);font-size:14px}
.fold-body>.meta{margin:0 0 8px}
.bad{color:#d14343}
.pagenav{display:flex;justify-content:center;flex-wrap:wrap;gap:8px;margin-top:32px}
.navbtn{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:8px 16px;font-size:14px;color:var(--text)}
footer .gen{color:var(--muted);font-size:13px;text-align:center;margin-top:16px}
.months h2{font-size:17px;color:var(--muted);margin:28px 0 8px}
.list{list-style:none;padding:0;margin:0}
.list a{display:block;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:8px 0;color:var(--text)}
.list .date{display:block;font-weight:700}
.list .desc{display:block;color:var(--muted);font-size:15px;margin-top:2px}
.empty{color:var(--muted)}
"""


def _document(title: str, description: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="light dark">\n'
        '<meta name="robots" content="noindex">\n'
        f'<meta name="description" content="{_e(description)}">\n'
        f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
        f'<body>\n<main class="wrap">\n{body}\n</main>\n</body>\n</html>\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# 페이지
# ─────────────────────────────────────────────────────────────────────────────
def render_page(
    b: Briefing,
    result: CollectResult,
    *,
    index_href: str = "../../index.html",
    prev_href: str | None = None,
    next_href: str | None = None,
    source_href: str | None = None,
) -> str:
    d = b.day
    body = ['<header>', '<p class="eyebrow">🌅 모닝 브리핑</p>', f"<h1>{_e(day_label(d))}</h1>"]
    if b.headline:
        body.append(f'<p class="headline">{_e(b.headline)}</p>')
    chips = []
    if b.picks:
        chips.append(f'<a href="#news">📰 오늘의 소식 {len(b.picks)}</a>')
    if b.analysis:
        count = f" {len(b.analysis.lenses)}" if b.analysis.lenses else ""
        chips.append(f'<a href="#lens">🧭 사업가의 시선{count}</a>')
    if chips:
        body.append(f'<nav class="chips">{"".join(chips)}</nav>')
    body.append("</header>")
    if b.error:
        body.append('<p class="warn">⚠️ LLM 분석에 실패해 기본 정렬(동시 보도 수·HN 점수·최신순)로 대신 골랐습니다.</p>')

    body += ['<section id="news">', "<h2>📰 오늘의 소식</h2>"]
    if b.picks:
        body += [_pick(rank, p) for rank, p in enumerate(b.picks, 1)]
    else:
        body.append('<p class="empty">오늘은 기준 점수를 넘은 기사가 없습니다.</p>')
    body.append("</section>")
    if b.analysis:
        body.append(_analysis(b.analysis))
    if b.candidates:
        body.append(_candidates(b))
    body.append(_sources(result))

    nav = []
    if prev_href:
        nav.append(f'<a class="navbtn" href="{_e(prev_href)}">← 이전 브리핑</a>')
    nav.append(f'<a class="navbtn" href="{_e(index_href)}">모아보기</a>')
    nav.append(f'<a class="navbtn" href="{_e(next_href)}">다음 브리핑 →</a>' if next_href else NEXT_SLOT)
    gen = f"생성 {b.generated_at.astimezone(KST):%Y-%m-%d %H:%M} KST"
    if source_href:
        gen += f' · <a href="{_href(source_href)}">마크다운 원본</a>'
    body.append(f'<footer><nav class="pagenav">{"".join(nav)}</nav><p class="gen">{gen}</p></footer>')

    title = f"모닝 브리핑 · {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"
    return _document(title, b.headline or day_label(d), "\n".join(body))


def render_index(docs_dir: Path) -> str:
    months: dict[str, list[str]] = {}
    for day, path in reversed(_pages(docs_dir)):
        match = _DESC_RE.search(path.read_text(encoding="utf-8")[:4000])
        desc = html.unescape(match.group(1)) if match else ""
        href = path.relative_to(docs_dir).as_posix()
        months.setdefault(f"{day.year}년 {day.month}월", []).append(
            f'<li><a href="{_e(href)}"><span class="date">{_e(day_label(day))}</span>'
            f'<span class="desc">{_e(desc)}</span></a></li>'
        )
    body = ['<header><p class="eyebrow">🌅 모닝 브리핑</p><h1>모아보기</h1></header>', '<div class="months">']
    for month, items in months.items():
        body += [f"<h2>{_e(month)}</h2>", f'<ul class="list">{"".join(items)}</ul>']
    if not months:
        body.append('<p class="empty">아직 브리핑이 없습니다.</p>')
    body.append("</div>")
    return _document("모닝 브리핑 모아보기", "날짜별 모닝 브리핑", "\n".join(body))


def write_page(b: Briefing, result: CollectResult, docs_dir: Path, source_href: str | None = None) -> Path:
    """오늘 페이지를 쓰고, 전날 페이지에 '다음' 링크를 붙이고, 모아보기를 갱신한다."""
    path = page_path(docs_dir, b.day)
    path.parent.mkdir(parents=True, exist_ok=True)
    others = [(d, p) for d, p in _pages(docs_dir) if d != b.day]
    prev = max(((d, p) for d, p in others if d < b.day), default=None)
    nxt = min(((d, p) for d, p in others if d > b.day), default=None)
    rel = lambda p: "../../" + p.relative_to(docs_dir).as_posix()  # noqa: E731
    path.write_text(
        render_page(
            b, result,
            prev_href=rel(prev[1]) if prev else None,
            next_href=rel(nxt[1]) if nxt else None,
            source_href=source_href,
        ),
        encoding="utf-8",
    )
    if prev:
        text = prev[1].read_text(encoding="utf-8")
        if NEXT_SLOT in text:
            link = f'<a class="navbtn" href="{_e(rel(path))}">다음 브리핑 →</a>'
            prev[1].write_text(text.replace(NEXT_SLOT, link), encoding="utf-8")
    (docs_dir / ".nojekyll").touch()
    (docs_dir / "index.html").write_text(render_index(docs_dir), encoding="utf-8")
    return path
