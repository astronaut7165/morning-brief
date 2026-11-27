"""코인 시세와 시장 심리 지표를 모은다 (모두 키 없이 쓰는 공개 API).

  CoinGecko      달러 가격, 24시간·7일·30일·1년 변동률, 사상 최고가 대비, 글로벌 원화 환산가
  업비트          국내 원화 가격 → 김치 프리미엄(국내가 / 글로벌 원화 환산가 - 1)
  alternative.me 공포·탐욕 지수 (0 극단적 공포 ~ 100 극단적 탐욕)

일부가 실패해도 받은 만큼으로 진행하고, 실패 내용은 errors 에 남긴다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import requests

log = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3"
UPBIT_URL = "https://api.upbit.com/v1/ticker"
FNG_URL = "https://api.alternative.me/fng/"
UA = {"User-Agent": "morning-brief/1.0", "Accept": "application/json"}
FNG_LABELS = {
    "Extreme Fear": "극단적 공포",
    "Fear": "공포",
    "Neutral": "중립",
    "Greed": "탐욕",
    "Extreme Greed": "극단적 탐욕",
}


@dataclass
class Quote:
    symbol: str
    name: str
    usd: float | None = None
    change_24h: float | None = None  # %
    change_7d: float | None = None
    change_30d: float | None = None
    change_1y: float | None = None
    ath_change: float | None = None  # 사상 최고가 대비 %
    krw: float | None = None  # 업비트 원화 가격
    premium: float | None = None  # 김치 프리미엄 %


@dataclass
class Sentiment:
    value: int
    label: str
    week_ago: int | None = None


@dataclass
class Market:
    fetched_at: datetime
    quotes: list[Quote] = field(default_factory=list)
    sentiment: Sentiment | None = None
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any(q.usd is not None for q in self.quotes)


# ─────────────────────────────────────────────────────────────────────────────
# 표시용 문자열
# ─────────────────────────────────────────────────────────────────────────────
def pct(value: float | None, digits: int = 1) -> str:
    return "–" if value is None else f"{value:+.{digits}f}%"


def usd(value: float | None) -> str:
    if value is None:
        return "–"
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def krw(value: float | None) -> str:
    if value is None:
        return "–"
    if value >= 100_000_000:
        eok, man = divmod(int(value / 10_000 + 0.5), 10_000)  # 반올림 (round 는 짝수 쪽으로 붙음)
        return f"{eok}억 {man:,}만원" if man else f"{eok}억원"
    if value >= 10_000:
        return f"{int(value / 10_000 + 0.5):,}만원"
    return f"{value:,.0f}원"


def arrow(value: float | None) -> str:
    if value is None:
        return ""
    return "▲" if value > 0 else "▼" if value < 0 else "–"


def sentiment_label(s: Sentiment) -> str:
    text = f"{s.value} ({s.label})"
    if s.week_ago is not None:
        text += f" · 1주 전 {s.week_ago}"
    return text


def prompt_text(m: Market) -> str:
    """LLM 에 넘길 시세 요약. 분석은 이 수치만 인용한다."""
    lines = [f"기준 시각: {m.fetched_at:%Y-%m-%d %H:%M} KST (출처: CoinGecko, 업비트, alternative.me)"]
    for q in m.quotes:
        if q.usd is None:
            lines.append(f"{q.symbol} {q.name}: 시세를 받지 못함")
            continue
        parts = [
            usd(q.usd), f"24시간 {pct(q.change_24h)}", f"7일 {pct(q.change_7d)}",
            f"30일 {pct(q.change_30d)}", f"1년 {pct(q.change_1y)}", f"사상 최고가 대비 {pct(q.ath_change)}",
        ]
        if q.krw is not None:
            parts.append(f"업비트 {q.krw:,.0f}원 (김치 프리미엄 {pct(q.premium, 2)})")
        lines.append(f"{q.symbol} {q.name}: " + " | ".join(parts))
    if m.sentiment:
        lines.append(f"공포·탐욕 지수(코인 시장 전체): {sentiment_label(m.sentiment)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 수집
# ─────────────────────────────────────────────────────────────────────────────
def _get(url: str, **params) -> object:
    resp = requests.get(url, params=params, headers=UA, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_market(coins: list[dict], now: datetime) -> Market:
    """coins: [{symbol, name, coingecko, upbit}] (config 의 coins 항목)."""
    market = Market(fetched_at=now, quotes=[Quote(symbol=c["symbol"], name=c.get("name", c["symbol"])) for c in coins])
    by_gecko = {c["coingecko"]: q for c, q in zip(coins, market.quotes) if c.get("coingecko")}
    by_upbit = {c["upbit"]: q for c, q in zip(coins, market.quotes) if c.get("upbit")}
    global_krw: dict[str, float] = {}

    try:
        rows = _get(
            f"{COINGECKO_URL}/coins/markets", vs_currency="usd", ids=",".join(by_gecko),
            price_change_percentage="24h,7d,30d,1y",
        )
        for row in rows:
            if q := by_gecko.get(row.get("id")):
                q.usd = row.get("current_price")
                q.change_24h = row.get("price_change_percentage_24h_in_currency")
                q.change_7d = row.get("price_change_percentage_7d_in_currency")
                q.change_30d = row.get("price_change_percentage_30d_in_currency")
                q.change_1y = row.get("price_change_percentage_1y_in_currency")
                q.ath_change = row.get("ath_change_percentage")
        prices = _get(f"{COINGECKO_URL}/simple/price", ids=",".join(by_gecko), vs_currencies="krw")
        global_krw = {gid: v["krw"] for gid, v in prices.items() if isinstance(v, dict) and v.get("krw")}
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        market.errors.append(f"CoinGecko: {type(exc).__name__}")

    if by_upbit:
        try:
            for row in _get(UPBIT_URL, markets=",".join(by_upbit)):
                if q := by_upbit.get(row.get("market")):
                    q.krw = row.get("trade_price")
        except (requests.RequestException, ValueError, TypeError) as exc:
            market.errors.append(f"업비트: {type(exc).__name__}")
    for gid, q in by_gecko.items():
        if q.krw and global_krw.get(gid):
            q.premium = (q.krw / global_krw[gid] - 1) * 100

    try:
        data = _get(FNG_URL, limit=8)["data"]
        label = data[0].get("value_classification", "")
        market.sentiment = Sentiment(
            value=int(data[0]["value"]),
            label=FNG_LABELS.get(label, label),
            week_ago=int(data[7]["value"]) if len(data) > 7 else None,
        )
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError) as exc:
        market.errors.append(f"공포·탐욕 지수: {type(exc).__name__}")

    for err in market.errors:
        log.warning("시세 수집 일부 실패 — %s", err)
    ok = sum(q.usd is not None for q in market.quotes)
    log.info("시세 수집: %d/%d개 코인%s", ok, len(market.quotes), " · 공포·탐욕 지수" if market.sentiment else "")
    return market
