"""소스별 최근 N시간 기사 수집 및 중복 제거.

- 소스 정의는 config/sources.yaml 에만 있습니다. 이 파일은 '어떻게 가져오는가'만 압니다.
- 새 수집 방식은 @fetcher("<type>") 함수를 하나 추가하면 sources.yaml 에서 바로 쓸 수 있습니다.
"""

from __future__ import annotations

import calendar
import difflib
import hashlib
import html
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import feedparser
import requests

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; MorningBriefBot/1.0; RSS reader)"
REQUEST_TIMEOUT = 20
SUMMARY_MAX_CHARS = 400
TITLE_SIMILARITY = 0.88
AGGREGATOR_TYPES = {"google_news"}  # 중복 시 원문 소스를 우선 남기기 위해 뒤로 미룸


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 모델
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Article:
    title: str
    url: str
    source: str
    published: datetime  # UTC
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # points, comments, discussion_url, publisher ...
    also_in: list[tuple[str, str]] = field(default_factory=list)  # 같은 기사를 보도한 (소스, URL)

    def __post_init__(self) -> None:
        self.url = strip_tracking(self.url)

    @property
    def key(self) -> str:
        return hashlib.sha1(normalize_url(self.url).encode()).hexdigest()[:16]

    @property
    def coverage(self) -> int:
        """이 기사를 보도한 소스 수 (자기 자신 포함)."""
        return 1 + len(self.also_in)


@dataclass
class SourceStat:
    name: str
    fetched: int = 0
    error: str | None = None


@dataclass
class CollectResult:
    articles: list[Article]
    stats: list[SourceStat]
    total_fetched: int = 0
    duplicates: int = 0
    seen_before: int = 0

    @property
    def failed_sources(self) -> list[SourceStat]:
        return [s for s in self.stats if s.error]


# ─────────────────────────────────────────────────────────────────────────────
# 텍스트 / URL 정규화
# ─────────────────────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_WS_RE = re.compile(r"\s+")
_TRACKING_PARAM_RE = re.compile(
    r"^(utm_.*|fbclid|gclid|dclid|mc_cid|mc_eid|igshid|ref|ref_src|cmpid|ncid|spm|from)$", re.I
)
# 표시용 URL 에서 지울 파라미터 (기사 식별에는 영향이 없는 것만)
_DISPLAY_STRIP_RE = re.compile(r"^(utm_.*|fbclid|gclid|dclid|mc_cid|mc_eid|igshid)$", re.I)
_TITLE_NOISE_RE = re.compile(r"[^\w]+", re.UNICODE)
_HAS_TZ_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2}|\b[A-Z]{2,4})\s*$")


def clean_text(raw: str | None, limit: int | None = None) -> str:
    if not raw:
        return ""
    text = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", html.unescape(text)).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def strip_tracking(url: str) -> str:
    """utm_* 같은 추적 파라미터만 지운다. 해당 파라미터가 없으면 URL을 그대로 둔다."""
    parts = urlsplit(url.strip())
    if not parts.query:
        return url.strip()
    params = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in params if not _DISPLAY_STRIP_RE.match(k)]
    if len(kept) == len(params):
        return url.strip()
    return urlunsplit(parts._replace(query=urlencode(kept)))


def normalize_url(url: str) -> str:
    """추적 파라미터·www·끝 슬래시·fragment 를 제거해 같은 기사를 같은 문자열로 만든다."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _TRACKING_PARAM_RE.match(k)
        )
    )
    path = parts.path.rstrip("/") or "/"
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    return urlunsplit((scheme, host, path, query, ""))


def normalize_title(title: str) -> str:
    return _TITLE_NOISE_RE.sub(" ", title.lower()).strip()


def _to_utc(struct_time) -> datetime:
    return datetime.fromtimestamp(calendar.timegm(struct_time), tz=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Fetcher 레지스트리
# ─────────────────────────────────────────────────────────────────────────────
Fetcher = Callable[[dict, datetime], list[Article]]
FETCHERS: dict[str, Fetcher] = {}


def fetcher(type_name: str) -> Callable[[Fetcher], Fetcher]:
    def register(fn: Fetcher) -> Fetcher:
        FETCHERS[type_name] = fn
        return fn

    return register


def _http_get(url: str, params: dict | None = None, retries: int = 1) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url, params=params, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _parse_feed(src: dict, url: str, since: datetime) -> list[Article]:
    resp = _http_get(url)
    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"피드 파싱 실패: {feed.get('bozo_exception')}")

    naive_offset = src.get("naive_utc_offset")
    now = datetime.now(timezone.utc)
    articles: list[Article] = []
    undated = 0
    for entry in feed.entries:
        field_name = "published" if entry.get("published_parsed") else "updated"
        published_struct = entry.get(f"{field_name}_parsed")
        if not published_struct:
            undated += 1  # 발행 시각을 알 수 없으면 '최근 24시간' 여부를 보장할 수 없어 제외
            continue
        published = _to_utc(published_struct)
        # 시간대 표기 없이 현지 시각을 쓰는 피드 보정 (feedparser 는 이를 UTC로 해석함)
        if naive_offset is not None and not _HAS_TZ_RE.search(entry.get(field_name, "")):
            published -= timedelta(hours=float(naive_offset))
        if published > now + timedelta(hours=1):
            published = now  # 미래 시각은 피드 오류로 보고 현재 시각으로 맞춤
        if published < since:
            continue
        link = entry.get("link")
        title = clean_text(entry.get("title"))
        if not link or not title:
            continue
        body = entry.get("summary") or (entry.get("content") or [{}])[0].get("value", "")
        articles.append(
            Article(
                title=title,
                url=link,
                source=src["name"],
                published=published,
                summary=clean_text(body, SUMMARY_MAX_CHARS),
                tags=list(src.get("tags", [])),
            )
        )
    if undated:
        log.debug("%s: 발행 시각 없는 항목 %d건 제외", src["name"], undated)
    return articles


@fetcher("rss")
def fetch_rss(src: dict, since: datetime) -> list[Article]:
    return _parse_feed(src, src["url"], since)


@fetcher("google_news")
def fetch_google_news(src: dict, since: datetime) -> list[Article]:
    lang = src.get("lang", "ko")
    locale = {"ko": ("ko", "KR", "KR:ko"), "en": ("en-US", "US", "US:en")}.get(lang)
    if locale is None:
        raise ValueError(f"지원하지 않는 lang: {lang} (ko | en)")
    hl, gl, ceid = locale
    days = max(1, round((datetime.now(timezone.utc) - since).total_seconds() / 86400))
    query = quote_plus(f"{src['query']} when:{days}d")
    url = f"https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"

    articles = _parse_feed(src, url, since)
    for art in articles:
        # Google News 제목은 "기사 제목 - 언론사" 형태 → 언론사를 분리해 보관
        head, sep, publisher = art.title.rpartition(" - ")
        if sep and head:
            art.title = head
            art.extra["publisher"] = publisher
        art.summary = ""  # 요약 필드는 관련 기사 링크 목록이라 노이즈만 됨
    return articles


@fetcher("hackernews")
def fetch_hackernews(src: dict, since: datetime) -> list[Article]:
    min_points = int(src.get("min_points", 100))
    resp = _http_get(
        "https://hn.algolia.com/api/v1/search",
        params={
            "tags": "story",
            "numericFilters": f"created_at_i>{int(since.timestamp())},points>={min_points}",
            "hitsPerPage": 100,
        },
    )
    hits = sorted(resp.json().get("hits", []), key=lambda h: h.get("points") or 0, reverse=True)
    articles = []
    for hit in hits:
        discussion = f"https://news.ycombinator.com/item?id={hit['objectID']}"
        articles.append(
            Article(
                title=clean_text(hit.get("title")),
                url=hit.get("url") or discussion,
                source=src["name"],
                published=datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc),
                summary=clean_text(hit.get("story_text"), SUMMARY_MAX_CHARS),
                tags=list(src.get("tags", [])),
                extra={
                    "points": hit.get("points"),
                    "comments": hit.get("num_comments"),
                    "discussion_url": discussion,
                },
            )
        )
    return articles


# ─────────────────────────────────────────────────────────────────────────────
# 이전 브리핑과의 중복 제거 기록
# ─────────────────────────────────────────────────────────────────────────────
class SeenStore:
    """URL 해시 → 처음 본 날짜. '오늘 이전'에 본 기사만 제외하므로 같은 날 재실행해도 결과가 같다."""

    def __init__(self, path: Path, today: date, retention_days: int = 14):
        self.path = path
        self.today = today.isoformat()
        self.retention_days = retention_days
        self.data: dict[str, str] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("seen 기록을 읽지 못해 새로 시작합니다: %s", exc)

    def seen_before_today(self, key: str) -> bool:
        first_seen = self.data.get(key)
        return first_seen is not None and first_seen < self.today

    def mark(self, keys: list[str]) -> None:
        for key in keys:
            self.data.setdefault(key, self.today)

    def save(self) -> None:
        cutoff = (date.fromisoformat(self.today) - timedelta(days=self.retention_days)).isoformat()
        self.data = {k: v for k, v in sorted(self.data.items()) if v >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=0, sort_keys=True) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# 수집 파이프라인
# ─────────────────────────────────────────────────────────────────────────────
def enabled_sources(config: dict) -> list[dict]:
    """sources + 켜져 있는 tracks 의 소스를 합친 목록 (원문 소스 먼저, 애그리게이터 나중)."""
    sources = [s for s in config.get("sources") or [] if s.get("enabled", True)]
    for track_name, track in (config.get("tracks") or {}).items():
        if not track.get("enabled", False):
            continue
        for s in track.get("sources") or []:
            if s.get("enabled", True):
                sources.append({**s, "tags": list(s.get("tags", [])) + [f"track:{track_name}"]})
    return sorted(sources, key=lambda s: s.get("type", "rss") in AGGREGATOR_TYPES)


def _fetch_one(src: dict, since: datetime, default_max: int) -> tuple[list[Article], SourceStat]:
    stat = SourceStat(name=src.get("name", "?"))
    fetch = FETCHERS.get(src.get("type", "rss"))
    if fetch is None:
        stat.error = f"알 수 없는 type: {src.get('type')}"
        return [], stat
    try:
        items = fetch(src, since)
    except Exception as exc:  # 소스 하나의 실패가 전체를 멈추지 않도록
        stat.error = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("[%s] 수집 실패 — %s", stat.name, stat.error)
        return [], stat
    if src.get("type") != "hackernews":
        items.sort(key=lambda a: a.published, reverse=True)
    items = items[: int(src.get("max_items", default_max))]
    stat.fetched = len(items)
    log.info("[%s] %d건", stat.name, stat.fetched)
    return items, stat


def dedupe(articles: list[Article]) -> tuple[list[Article], int]:
    """URL·제목 유사도로 중복을 합친다. 먼저 나온 기사를 남기고 나머지는 also_in 에 기록."""
    kept: list[Article] = []
    by_url: dict[str, Article] = {}
    titles: list[tuple[str, Article]] = []
    removed = 0

    for art in articles:
        url_key = normalize_url(art.url)
        title_key = normalize_title(art.title)
        match = by_url.get(url_key)
        if match is None and title_key:
            for other_title, other in titles:
                sm = difflib.SequenceMatcher(None, title_key, other_title)
                if sm.real_quick_ratio() >= TITLE_SIMILARITY and sm.quick_ratio() >= TITLE_SIMILARITY:
                    if sm.ratio() >= TITLE_SIMILARITY:
                        match = other
                        break
        if match is not None:
            removed += 1
            if art.source != match.source and all(s != art.source for s, _ in match.also_in):
                match.also_in.append((art.source, art.url))
            for k, v in art.extra.items():
                match.extra.setdefault(k, v)
            if len(art.summary) > len(match.summary):
                match.summary = art.summary
            continue
        kept.append(art)
        by_url[url_key] = art
        titles.append((title_key, art))
    return kept, removed


def collect(config: dict, now: datetime, seen: SeenStore | None = None) -> CollectResult:
    settings = config.get("settings") or {}
    since = now - timedelta(hours=float(settings.get("lookback_hours", 24)))
    default_max = int(settings.get("max_items_per_source", 15))
    sources = enabled_sources(config)
    log.info("수집 시작: 소스 %d개, 기준 %s 이후", len(sources), since.isoformat(timespec="minutes"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: _fetch_one(s, since, default_max), sources))

    raw: list[Article] = [a for items, _ in results for a in items]
    stats = [stat for _, stat in results]
    articles, dup_count = dedupe(raw)

    seen_count = 0
    if seen is not None:
        fresh = [a for a in articles if not seen.seen_before_today(a.key)]
        seen_count = len(articles) - len(fresh)
        articles = fresh

    log.info(
        "수집 완료: 원본 %d건 → 중복 %d건 · 이전 브리핑 %d건 제외 → %d건",
        len(raw), dup_count, seen_count, len(articles),
    )
    return CollectResult(
        articles=articles,
        stats=stats,
        total_fetched=len(raw),
        duplicates=dup_count,
        seen_before=seen_count,
    )
