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

import market as mk
from analyzer import KST, VIEW_ARROWS, Analysis, BriefInfo, Briefing, Outlook, Pick
from archiver import (
    FOLD_AT_OR_BELOW,
    OUTLOOK_NOTE,
    OUTLOOK_TITLE,
    WEEKDAYS,
    candidate_groups,
    coin_name,
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


def page_marker(b: Briefing) -> str:
    """페이지마다 박아 두는 생성 시각. 같은 날 다시 만들었을 때 새 버전이 배포됐는지 구분한다."""
    return b.generated_at.isoformat(timespec="seconds")


def wait_until_live(url: str, timeout: float = 300, interval: float = 10, expect: str | None = None) -> bool:
    """push 직후 GitHub Pages 배포가 끝나 페이지가 열릴 때까지 기다린다. expect 가 있으면 그 문자열이 보일 때까지."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            # 캐시된 404 를 피하려고 매번 다른 주소로 확인
            resp = requests.get(url, params={"t": time.time_ns()}, timeout=15)
            if resp.status_code == 200 and (expect is None or expect in resp.text):
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
    # 같은 매체가 여러 번 나와도 구분되도록 제목을 함께 보여 준다 (동시 보도는 매체명만 있음)
    related = [(r.title, r.source, r.url) for r in pick.related] + [("", s, u) for s, u in art.also_in]
    if related:
        items = "".join(
            f"<li>{_a(title or source, url)}" + (f"<small>{_e(source)}</small>" if title else "") + "</li>"
            for title, source, url in related
        )
        out.append(f'<details class="related"><summary>관련 보도 {len(related)}건</summary><ul>{items}</ul></details>')
    out.append("</article>")
    return "\n".join(out)


def _trend(value: float | None) -> str:
    return "up" if value and value > 0 else "down" if value and value < 0 else "flat"


def _market(b: Briefing) -> str:
    m = b.market
    out = ['<section id="market">', "<h2>💹 시세</h2>"]
    if not m:
        out += ['<p class="empty">시세를 가져오지 못했습니다.</p>', "</section>"]
        return "\n".join(out)
    out.append('<div class="quotes">')
    for q in m.quotes:
        stats = [
            ("7일", mk.pct(q.change_7d), _trend(q.change_7d)),
            ("30일", mk.pct(q.change_30d), _trend(q.change_30d)),
            ("1년", mk.pct(q.change_1y), _trend(q.change_1y)),
            ("최고가 대비", mk.pct(q.ath_change), _trend(q.ath_change)),
            ("업비트", mk.krw(q.krw), ""),
            ("김프", mk.pct(q.premium, 2), _trend(q.premium)),
        ]
        cells = "".join(f'<div><dt>{label}</dt><dd class="{cls}">{_e(value)}</dd></div>' for label, value, cls in stats)
        change = f"{mk.arrow(q.change_24h)}{abs(q.change_24h):.1f}%" if q.change_24h is not None else "–"
        out.append(
            '<div class="card quote">'
            f'<div class="q-head"><p class="q-name">{_e(q.name)} <span>{_e(q.symbol)}</span></p>'
            f'<p class="q-price">{_e(mk.usd(q.usd))}</p>'
            f'<p class="q-chg {_trend(q.change_24h)}">{_e(change)}<small>24시간</small></p></div>'
            f'<dl class="q-stats">{cells}</dl></div>'
        )
    out.append("</div>")
    if s := m.sentiment:
        week = f" · 1주 전 {s.week_ago}" if s.week_ago is not None else ""
        out.append(
            '<div class="card fng">'
            f'<p class="fng-top"><span>공포·탐욕 지수</span><b>{s.value}</b><span class="fng-label">{_e(s.label)}{week}</span></p>'
            f'<div class="gauge"><i style="left:{max(0, min(100, s.value))}%"></i></div>'
            '<p class="gauge-ends"><span>극단적 공포</span><span>극단적 탐욕</span></p></div>'
        )
    note = f"기준 {m.fetched_at.astimezone(KST):%m월 %d일 %H:%M} · CoinGecko·업비트·alternative.me"
    if m.errors:
        note += " · 일부 실패: " + ", ".join(m.errors)
    out += [f'<p class="meta">{_e(note)}</p>', "</section>"]
    return "\n".join(out)


_VIEW_CLASS = {"상승 우위": "up", "하락 우위": "down"}


def _outlook(b: Briefing, outlook: Outlook) -> str:
    out = ['<section id="outlook">', f"<h2>{_e(OUTLOOK_TITLE)}</h2>"]
    if outlook.mood:
        out.append(f'<div class="overview"><p class="label">지금 분위기</p><p>{_e(outlook.mood)}</p></div>')
    for c in outlook.coins:
        rows = []
        for label, view, conf, reason in (
            ("단기 · 1~4주", c.short_view, c.short_confidence, c.short_reason),
            ("장기 · 6개월~", c.long_view, c.long_confidence, c.long_reason),
        ):
            badge = f'<span class="view {_VIEW_CLASS.get(view, "flat")}">{_e(VIEW_ARROWS.get(view, ""))} {_e(view)}</span>'
            conf_html = f'<span class="conf">확신 {_e(conf)}</span>' if conf else ""
            rows.append(
                f'<div class="horizon"><p class="h-top"><span>{label}</span><span class="badges">{conf_html}{badge}</span></p>'
                f"<p>{_e(reason)}</p></div>"
            )
        signal = f'<p class="signal">🚩 판단이 바뀌는 신호 — {_e(c.signal)}</p>' if c.signal else ""
        out.append(f'<article class="card coin"><h3>{_e(coin_name(b, c.symbol))}</h3>{"".join(rows)}{signal}</article>')
    out += [f'<p class="note">{_e(OUTLOOK_NOTE)}</p>', "</section>"]
    return "\n".join(out)


def _analysis(info: BriefInfo, analysis: Analysis) -> str:
    out = ['<section id="lens">', f"<h2>{_e(info.analysis_title)}</h2>"]
    if info.analysis_subtitle:
        out.append(f'<p class="sub">{_e(info.analysis_subtitle)}</p>')
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
:root{--bg:#f5f5f3;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--up:#d63c3c;--down:#2e6bd9;
--accent:#2563eb;--accent-soft:#eef3ff;--lens:#6d4ae6;--lens-soft:#f3effe;--warn-bg:#fff4e5;--warn:#8a4b00}
@media (prefers-color-scheme:dark){:root{--bg:#0f1012;--card:#1a1b1e;--text:#ececef;--muted:#a1a1aa;--line:#2b2c31;--up:#ff6b6b;--down:#6ea2ff;
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
details.related{margin-top:12px;border-top:1px solid var(--line)}
details.related>summary{padding:10px 0 0;font-size:14px;font-weight:600;color:var(--muted)}
.related ul{list-style:none;padding:0;margin:6px 0 0}
.related li{padding:8px 0;border-top:1px dashed var(--line);font-size:15px;line-height:1.5}
.related li small{display:block;color:var(--muted);font-size:13px;margin-top:2px}
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
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--muted)}
.quotes .card{margin:10px 0}
.q-head{display:flex;align-items:baseline;gap:10px}
.q-name{font-weight:700;flex:1}
.q-name span{color:var(--muted);font-weight:500;font-size:14px}
.q-price{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}
.q-chg{font-weight:700;font-variant-numeric:tabular-nums;text-align:right;min-width:64px}
.q-chg small{display:block;color:var(--muted);font-weight:400;font-size:12px;line-height:1.2}
.q-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px 12px;margin:12px 0 0;padding-top:12px;border-top:1px solid var(--line)}
.q-stats dt{color:var(--muted);font-size:12px}
.q-stats dd{margin:0;font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.fng-top{display:flex;align-items:baseline;gap:10px}
.fng-top span:first-child{flex:1;font-weight:700}
.fng-top b{font-size:22px}
.fng-label{color:var(--muted);font-size:14px}
.gauge{position:relative;height:8px;border-radius:99px;margin:14px 0 6px;
background:linear-gradient(90deg,var(--down),#9aa3ad 50%,var(--up))}
.gauge i{position:absolute;top:-4px;width:4px;height:16px;margin-left:-2px;border-radius:2px;background:var(--text)}
.gauge-ends{display:flex;justify-content:space-between;color:var(--muted);font-size:12px}
.coin h3{margin-bottom:4px}
.horizon{padding:12px 0 0;margin-top:12px;border-top:1px solid var(--line)}
.h-top{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;color:var(--muted);font-size:14px;font-weight:600}
.view{border-radius:999px;padding:2px 10px;font-size:14px;font-weight:700;background:var(--bg)}
.badges{display:flex;align-items:center;gap:6px}
.conf{color:var(--muted);font-size:12px;font-weight:500}
.signal{margin-top:12px;padding:10px 12px;background:var(--bg);border-radius:10px;font-size:15px}
.note{color:var(--muted);font-size:13px;margin-top:8px}
"""


def _document(title: str, description: str, body: str, marker: str = "") -> str:
    return (
        "<!doctype html>\n"
        '<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="light dark">\n'
        '<meta name="robots" content="noindex">\n'
        f'<meta name="description" content="{_e(description)}">\n'
        + (f'<meta name="brief-generated" content="{_e(marker)}">\n' if marker else "")
        + f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
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
    d, info = b.day, b.info
    body = ["<header>", f'<p class="eyebrow">{_e(info.emoji)} {_e(info.title)}</p>', f"<h1>{_e(day_label(d))}</h1>"]
    if b.headline:
        body.append(f'<p class="headline">{_e(b.headline)}</p>')
    chips = []
    if b.market is not None:
        chips.append('<a href="#market">💹 시세</a>')
    if b.picks:
        chips.append(f'<a href="#news">📰 오늘의 소식 {len(b.picks)}</a>')
    if b.outlook:
        chips.append(f'<a href="#outlook">{_e(OUTLOOK_TITLE)}</a>')
    if b.analysis:
        count = f" {len(b.analysis.lenses)}" if b.analysis.lenses else ""
        chips.append(f'<a href="#lens">{_e(info.analysis_title)}{count}</a>')
    if chips:
        body.append(f'<nav class="chips">{"".join(chips)}</nav>')
    body.append("</header>")
    if b.error:
        body.append('<p class="warn">⚠️ LLM 분석에 실패해 기본 정렬(동시 보도 수·HN 점수·최신순)로 대신 골랐습니다.</p>')

    if b.market is not None:
        body.append(_market(b))
    body += ['<section id="news">', "<h2>📰 오늘의 소식</h2>"]
    if b.picks:
        body += [_pick(rank, p) for rank, p in enumerate(b.picks, 1)]
    else:
        body.append('<p class="empty">오늘은 기준 점수를 넘은 기사가 없습니다.</p>')
    body.append("</section>")
    if b.outlook:
        body.append(_outlook(b, b.outlook))
    if b.analysis:
        body.append(_analysis(info, b.analysis))
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

    title = f"{info.title} · {d.month}월 {d.day}일 ({WEEKDAYS[d.weekday()]})"
    return _document(title, b.headline or day_label(d), "\n".join(body), marker=page_marker(b))


def render_index(docs_dir: Path, info: BriefInfo | None = None) -> str:
    info = info or BriefInfo()
    months: dict[str, list[str]] = {}
    for day, path in reversed(_pages(docs_dir)):
        match = _DESC_RE.search(path.read_text(encoding="utf-8")[:4000])
        desc = html.unescape(match.group(1)) if match else ""
        href = path.relative_to(docs_dir).as_posix()
        months.setdefault(f"{day.year}년 {day.month}월", []).append(
            f'<li><a href="{_e(href)}"><span class="date">{_e(day_label(day))}</span>'
            f'<span class="desc">{_e(desc)}</span></a></li>'
        )
    body = [f'<header><p class="eyebrow">{_e(info.emoji)} {_e(info.title)}</p><h1>모아보기</h1></header>', '<div class="months">']
    for month, items in months.items():
        body += [f"<h2>{_e(month)}</h2>", f'<ul class="list">{"".join(items)}</ul>']
    if not months:
        body.append('<p class="empty">아직 브리핑이 없습니다.</p>')
    body.append("</div>")
    return _document(f"{info.title} 모아보기", f"날짜별 {info.title}", "\n".join(body))


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
    (docs_dir / "index.html").write_text(render_index(docs_dir, b.info), encoding="utf-8")
    return path
