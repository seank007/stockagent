"""stockagent 웹 대시보드 (Bloomberg Terminal 풍 다크테마).

루트(/)        : 매매 루프 상태/포트폴리오/판단기록/PnL/차트
/analyze       : 종목 분석 페이지 (AI 정보형 리포트 + 차트)

실행:  python web.py   →  http://localhost:8000
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html as html_lib
import io
import json
import re
import time
import threading
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

import pyupbit
from flask import Flask, Response, jsonify, request

import config
import db
from agent.analysis import analyze
from agent.providers import get_provider
from brokers.upbit import UpbitBroker
from main import trading_loop
from state import store

app = Flask(__name__)
_broker_for_web: UpbitBroker | None = None
_trading_thread: threading.Thread | None = None
_analysis_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_coin_markets_cache: tuple[float, list[dict]] | None = None
_coin_mini_charts_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_coin_quote_cache: dict[str, tuple[float, dict]] = {}
_coin_orderbook_cache: dict[str, tuple[float, dict]] = {}
_coin_candles_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}
_current_prices_cache: dict[tuple[str, ...], tuple[float, dict[str, float]]] = {}
_ticker_quotes_cache: tuple[float, dict] | None = None
_portfolio_cache: tuple[float, dict] | None = None
_coin_news_cache: tuple[float, dict] | None = None
_coin_news_summary_cache: tuple[float, dict] | None = None
_upbit_public_lock = threading.Lock()
_upbit_public_next_call = 0.0
ANALYSIS_CACHE_SECONDS = 300
COIN_MARKETS_CACHE_SECONDS = 600
COIN_MINI_CHART_CACHE_SECONDS = 180
COIN_QUOTE_CACHE_SECONDS = 1.2
COIN_ORDERBOOK_CACHE_SECONDS = 1.2
COIN_CANDLES_CACHE_SECONDS = 25
CURRENT_PRICES_CACHE_SECONDS = 1.5
TICKER_QUOTES_CACHE_SECONDS = 20
PORTFOLIO_CACHE_SECONDS = 5
COIN_NEWS_CACHE_SECONDS = 60
COIN_NEWS_SUMMARY_CACHE_SECONDS = 120
UPBIT_PUBLIC_MIN_INTERVAL_SECONDS = 0.105
COIN_NEWS_DEFAULT_LIMIT = 48
COIN_NEWS_FEED_TIMEOUT_SECONDS = 6
COIN_NEWS_FEED_WORKERS = 9
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 stockagent/1.0"}
STOCK_PRESETS = [
    {"code": "005930", "name": "삼성전자"},
    {"code": "000660", "name": "SK하이닉스"},
    {"code": "035420", "name": "NAVER"},
    {"code": "005380", "name": "현대차"},
    {"code": "035720", "name": "카카오"},
]
COIN_NEWS_FEEDS = [
    {
        "name": "Google KR",
        "url": (
            "https://news.google.com/rss/search?"
            "q=%EB%B9%84%ED%8A%B8%EC%BD%94%EC%9D%B8%20OR%20%EC%95%94%ED%98%B8%ED%99%94%ED%8F%90%20OR%20%EA%B0%80%EC%83%81%EC%9E%90%EC%82%B0"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
        "filter": True,
        "limit": 36,
    },
    {
        "name": "Google Global",
        "url": "https://news.google.com/rss/search?q=bitcoin%20OR%20ethereum%20OR%20crypto&hl=en-US&gl=US&ceid=US:en",
        "filter": True,
        "limit": 36,
    },
    {"name": "Blockmedia", "url": "https://www.blockmedia.co.kr/feed/", "filter": True},
    {"name": "TokenPost", "url": "https://www.tokenpost.kr/rss", "filter": True, "limit": 24},
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "NewsBTC", "url": "https://www.newsbtc.com/feed/"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
]
CRYPTO_NEWS_KEYWORDS = (
    "비트코인", "이더리움", "리플", "솔라나", "도지", "테더", "스테이블코인", "가상자산", "암호화폐",
    "코인", "블록체인", "업비트", "빗썸", "코빗", "토큰", "디파이", "채굴", "김치프리미엄",
    "btc", "bitcoin", "eth", "ethereum", "xrp", "solana", "doge", "usdt", "stablecoin",
    "crypto", "cryptocurrency", "blockchain", "upbit", "bithumb", "token", "defi", "web3",
)
CRYPTO_NEWS_EXCLUDE_KEYWORDS = (
    "crypto prediction market",
    "prediction market - robinhood",
)
COIN_NEWS_SUMMARY_SYSTEM = """당신은 stockagent의 암호화폐 뉴스 편집자다.
제공된 최신 뉴스 목록만 근거로 한국어 시장 브리핑을 만든다.
투자 권유나 확정적 가격 예측은 하지 않는다. 중요한 변화, 리스크, 관찰 포인트를 짧고 선명하게 정리한다."""
COIN_NEWS_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "전체 뉴스를 관통하는 한 문장 제목"},
        "market_mood": {"type": "string", "description": "시장 분위기: 강세/약세/혼조/관망 등"},
        "brief": {
            "type": "array",
            "items": {"type": "string"},
            "description": "핵심 요약 3~5개",
        },
        "key_assets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "뉴스에서 많이 언급되거나 중요해 보이는 코인/자산",
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "주의할 리스크 2~4개",
        },
        "watch": {
            "type": "array",
            "items": {"type": "string"},
            "description": "앞으로 볼 신호 2~4개",
        },
        "source_note": {"type": "string", "description": "요약에 사용한 뉴스 범위"},
    },
    "required": ["headline", "market_mood", "brief", "key_assets", "risks", "watch", "source_note"],
    "additionalProperties": False,
}


def _broker() -> UpbitBroker:
    global _broker_for_web
    if _broker_for_web is None:
        _broker_for_web = UpbitBroker()
    return _broker_for_web


def start_background_trading() -> threading.Thread | None:
    """production/dev entrypoint에서 매매 루프를 정확히 한 번만 시작한다."""
    global _trading_thread
    if not config.RUN_TRADING_LOOP:
        return None
    if _trading_thread and _trading_thread.is_alive():
        return _trading_thread
    config.validate()
    _trading_thread = threading.Thread(
        target=trading_loop,
        daemon=True,
        name="stockagent-trading-loop",
    )
    _trading_thread.start()
    return _trading_thread


def _int_arg(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _csv_response(filename: str, rows: list[dict], fields: list[str]) -> Response:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        out.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _compact_reasoning_rows(rows: list[dict]) -> list[dict]:
    max_chars = int(getattr(config, "API_REASON_MAX_CHARS", 360))
    out = []
    for row in rows:
        item = dict(row)
        if "reasoning" in item:
            text = " ".join(str(item.get("reasoning") or "").split())
            if len(text) > max_chars:
                text = text[: max_chars - 1] + "..."
            item["reasoning"] = text
        out.append(item)
    return out


def _usable_ai_key(value: str) -> bool:
    key = str(value or "").strip()
    lowered = key.lower()
    if not key:
        return False
    placeholders = ("your-", "your_", "placeholder", "example", "changeme", "api-key")
    return not any(marker in lowered for marker in placeholders)


def _current_prices(tickers: list[str]) -> dict[str, float]:
    if not tickers:
        return {}
    key = tuple(sorted(set(tickers)))
    now = time.time()
    cached = _current_prices_cache.get(key)
    if cached and now - cached[0] < CURRENT_PRICES_CACHE_SECONDS:
        return cached[1]
    try:
        prices = pyupbit.get_current_price(list(key))
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(prices, dict):
        out = {k: float(v) for k, v in prices.items() if v is not None}
    elif len(key) == 1 and prices is not None:
        out = {key[0]: float(prices)}
    else:
        out = {}
    _current_prices_cache[key] = (now, out)
    return out


def _coin_markets(force: bool = False) -> list[dict]:
    """업비트 KRW 마켓 전체 목록. 봇 대상 종목과 분리해서 차트 UI에만 사용한다."""
    global _coin_markets_cache
    now = time.time()
    if not force and _coin_markets_cache and now - _coin_markets_cache[0] < COIN_MARKETS_CACHE_SECONDS:
        return _coin_markets_cache[1]

    markets: list[dict] = []
    try:
        raw = pyupbit.get_tickers(fiat="KRW", verbose=True) or []
        for item in raw:
            if isinstance(item, dict):
                market = str(item.get("market") or "").upper()
                if not market.startswith("KRW-"):
                    continue
                symbol = market.replace("KRW-", "")
                markets.append({
                    "market": market,
                    "symbol": symbol,
                    "korean_name": item.get("korean_name") or symbol,
                    "english_name": item.get("english_name") or symbol,
                })
            elif isinstance(item, str) and item.upper().startswith("KRW-"):
                market = item.upper()
                symbol = market.replace("KRW-", "")
                markets.append({
                    "market": market,
                    "symbol": symbol,
                    "korean_name": symbol,
                    "english_name": symbol,
                })
    except Exception:  # noqa: BLE001
        markets = []

    if not markets:
        markets = [
            {
                "market": ticker,
                "symbol": ticker.replace("KRW-", ""),
                "korean_name": ticker.replace("KRW-", ""),
                "english_name": ticker.replace("KRW-", ""),
            }
            for ticker in config.TICKERS
            if ticker.startswith("KRW-")
        ]

    by_market = {m["market"]: m for m in markets}
    priority_candidates = [
        *config.TICKERS,
        "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE", "KRW-ADA",
        "KRW-SUI", "KRW-LINK", "KRW-AVAX", "KRW-DOT", "KRW-TRX", "KRW-BCH",
    ]
    priority = []
    for ticker in priority_candidates:
        if ticker in by_market and ticker not in priority:
            priority.append(ticker)
    ordered = [by_market[ticker] for ticker in priority]
    ordered.extend(sorted((m for m in markets if m["market"] not in priority), key=lambda m: m["market"]))
    _coin_markets_cache = (now, ordered)
    return ordered


def _throttle_upbit_public() -> None:
    global _upbit_public_next_call
    with _upbit_public_lock:
        now = time.monotonic()
        wait = _upbit_public_next_call - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _upbit_public_next_call = max(now, _upbit_public_next_call) + UPBIT_PUBLIC_MIN_INTERVAL_SECONDS


def _upbit_candle_closes(ticker: str, interval: str, count: int) -> list[float]:
    """pyupbit가 빈 DataFrame을 반환할 때가 있어 미니 차트는 REST를 우선 사용한다."""
    minute_units = {
        "minute1": "1",
        "minute3": "3",
        "minute5": "5",
        "minute10": "10",
        "minute15": "15",
        "minute30": "30",
        "minute60": "60",
    }
    if interval in minute_units:
        endpoint = f"https://api.upbit.com/v1/candles/minutes/{minute_units[interval]}"
    elif interval == "day":
        endpoint = "https://api.upbit.com/v1/candles/days"
    else:
        endpoint = "https://api.upbit.com/v1/candles/minutes/60"

    query = urllib.parse.urlencode({"market": ticker, "count": count})
    _throttle_upbit_public()
    raw = _urlopen_text(f"{endpoint}?{query}", timeout=8)
    payload = json.loads(raw)
    if not isinstance(payload, list) or not payload:
        return []
    closes = [
        float(item["trade_price"])
        for item in reversed(payload)
        if isinstance(item, dict) and item.get("trade_price") is not None
    ]
    return closes


def _series_ma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    running = 0.0
    for idx, value in enumerate(values):
        running += value
        if idx >= window:
            running -= values[idx - window]
        out.append(running / window if idx >= window - 1 else None)
    return out


def _series_rsi(values: list[float], window: int = 14) -> list[float | None]:
    gains: list[float] = []
    losses: list[float] = []
    out: list[float | None] = [None]
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
        if idx < window:
            out.append(None)
            continue
        avg_gain = sum(gains[-window:]) / window
        avg_loss = sum(losses[-window:]) / window
        rs = avg_gain / (avg_loss or 1e-9)
        out.append(100 - (100 / (1 + rs)))
    return out


def _coin_candles_payload(ticker: str, interval: str, count: int) -> dict:
    cache_key = (ticker.upper(), interval, count)
    now = time.time()
    cached = _coin_candles_cache.get(cache_key)
    if cached and now - cached[0] < COIN_CANDLES_CACHE_SECONDS:
        return {**cached[1], "cached": True}

    closes: list[float] = []
    times: list[str] = []
    try:
        minute_units = {
            "minute1": "1", "minute3": "3", "minute5": "5", "minute10": "10",
            "minute15": "15", "minute30": "30", "minute60": "60",
        }
        if interval in minute_units:
            endpoint = f"https://api.upbit.com/v1/candles/minutes/{minute_units[interval]}"
        elif interval == "day":
            endpoint = "https://api.upbit.com/v1/candles/days"
        else:
            endpoint = "https://api.upbit.com/v1/candles/minutes/60"
        query = urllib.parse.urlencode({"market": ticker, "count": count})
        _throttle_upbit_public()
        raw = _urlopen_text(f"{endpoint}?{query}", timeout=8)
        rows = json.loads(raw)
        if isinstance(rows, list):
            for item in reversed(rows):
                if not isinstance(item, dict) or item.get("trade_price") is None:
                    continue
                closes.append(float(item["trade_price"]))
                times.append(item.get("candle_date_time_kst") or item.get("candle_date_time_utc") or "")
    except Exception:
        closes = []
        times = []

    if len(closes) < 2:
        df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
        if df is None or df.empty:
            raise ValueError(f"{ticker} 캔들 조회 실패")
        close = df["close"]
        closes = [float(close.iloc[i]) for i in range(len(df))]
        times = [str(df.index[i]) for i in range(len(df))]

    payload = {
        "ticker": ticker,
        "interval": interval,
        "closes": closes,
        "ma5": _series_ma(closes, 5),
        "ma20": _series_ma(closes, 20),
        "rsi": _series_rsi(closes, 14),
        "times": times,
        "cached": False,
    }
    _coin_candles_cache[cache_key] = (now, payload)
    return payload


def _urlopen_text(url: str, encoding: str = "utf-8", timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode(encoding, errors="ignore")


def _urlopen_json(url: str) -> dict:
    return json.loads(_urlopen_text(url))


def _stock_code(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 6:
        raise ValueError("국내 주식 종목코드 6자리를 입력하세요")
    return digits


def _number_from_text(value) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").replace("원", "").replace("배", "").strip()
    text = re.sub(r"[^0-9.\-+]", "", text)
    if not text or text in {"+", "-", ".", "+.", "-."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _moving_average(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            chunk = values[i + 1 - window:i + 1]
            out.append(sum(chunk) / window)
    return out


def _rsi(values: list[float], window: int = 14) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i < window:
            out.append(None)
            continue
        gains = 0.0
        losses = 0.0
        for j in range(i - window + 1, i + 1):
            diff = values[j] - values[j - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / window
        avg_loss = losses / window
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100 - (100 / (1 + rs)))
    return out


def _series_payload(ticker: str, interval: str, closes: list[float], times: list[str]) -> dict:
    return {
        "ticker": ticker,
        "interval": interval,
        "closes": closes,
        "ma5": _moving_average(closes, 5),
        "ma20": _moving_average(closes, 20),
        "rsi": _rsi(closes),
        "times": times,
    }


def _stock_chart(code: str, timeframe: str, count: int) -> dict:
    timeframe = timeframe if timeframe in {"day", "week", "month"} else "day"
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={code}&timeframe={timeframe}&requestType=0&count={count}"
    )
    xml_text = _urlopen_text(url, encoding="euc-kr")
    root = ET.fromstring(xml_text)
    closes: list[float] = []
    times: list[str] = []
    volumes: list[float] = []
    for item in root.findall(".//item"):
        parts = (item.attrib.get("data") or "").split("|")
        if len(parts) < 6:
            continue
        times.append(parts[0])
        closes.append(float(parts[4]))
        volumes.append(float(parts[5]))
    payload = _series_payload(code, timeframe, closes, times)
    payload["volumes"] = volumes
    return payload


def _stock_quote(code: str) -> dict:
    basic = _urlopen_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    integration = _urlopen_json(f"https://m.stock.naver.com/api/stock/{code}/integration")
    info = {row.get("code"): row for row in integration.get("totalInfos") or []}

    def info_value(key: str) -> str:
        return str((info.get(key) or {}).get("value") or "")

    rows = [
        {"label": "고가", "value": info_value("highPrice")},
        {"label": "현재", "value": basic.get("closePrice")},
        {"label": "저가", "value": info_value("lowPrice")},
        {"label": "전일", "value": info_value("lastClosePrice")},
        {"label": "시가", "value": info_value("openPrice")},
        {"label": "거래량", "value": info_value("accumulatedTradingVolume")},
    ]
    metrics = [
        {"label": "시총", "value": info_value("marketValue")},
        {"label": "외인소진율", "value": info_value("foreignRate")},
        {"label": "PER", "value": info_value("per")},
        {"label": "PBR", "value": info_value("pbr")},
    ]
    consensus = integration.get("consensusInfo") or {}
    if consensus:
        metrics.append({"label": "목표가", "value": consensus.get("priceTargetMean")})
        metrics.append({"label": "투자의견", "value": consensus.get("recommMean")})

    return {
        "code": code,
        "name": basic.get("stockName") or integration.get("stockName") or code,
        "price": _number_from_text(basic.get("closePrice")),
        "price_text": basic.get("closePrice") or "—",
        "change": _number_from_text(basic.get("compareToPreviousClosePrice")),
        "change_text": basic.get("compareToPreviousClosePrice") or "—",
        "change_pct": _number_from_text(basic.get("fluctuationsRatio")),
        "status": basic.get("marketStatus") or "—",
        "updated_at": basic.get("localTradedAt") or "—",
        "rows": rows,
        "metrics": metrics,
        "deal_trends": (integration.get("dealTrendInfos") or [])[:5],
    }


def _strip_html(value: str | None, max_len: int = 220) -> str:
    text = html_lib.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len - 1] + "…" if len(text) > max_len else text


def _child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(element):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def _child_attr(element: ET.Element, names: tuple[str, ...], attr: str) -> str:
    for child in list(element):
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.attrib.get(attr):
            return child.attrib[attr].strip()
    return ""


def _news_timestamp(value: str) -> float:
    if not value:
        return 0.0
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _rss_items(source: dict, limit: int = 24) -> tuple[list[dict], str | None]:
    try:
        root = ET.fromstring(_urlopen_text(source["url"], timeout=COIN_NEWS_FEED_TIMEOUT_SECONDS))
    except Exception as e:  # noqa: BLE001
        return [], str(e)

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    rows: list[dict] = []
    for item in items[:limit]:
        title = _strip_html(_child_text(item, ("title",)), max_len=160)
        link = _child_text(item, ("link",))
        if not link:
            link = _child_attr(item, ("link",), "href")
        publisher = _strip_html(_child_text(item, ("source", "creator")), max_len=80)
        description = _strip_html(
            _child_text(item, ("description", "summary", "encoded", "content")),
            max_len=260,
        )
        published = _child_text(item, ("pubdate", "published", "updated", "date"))
        image = _child_attr(item, ("content", "thumbnail", "enclosure"), "url")
        if not title or not link:
            continue
        rows.append({
            "source": source["name"],
            "publisher": publisher,
            "title": title,
            "link": link,
            "summary": description,
            "published": published,
            "published_ts": _news_timestamp(published),
            "image": image,
            "live": bool(source.get("live") or source["name"].startswith("Google")),
        })
    return rows, None


def _looks_like_crypto_news(row: dict) -> bool:
    text = f"{row.get('title') or ''} {row.get('summary') or ''}".lower()
    if any(keyword in text for keyword in CRYPTO_NEWS_EXCLUDE_KEYWORDS):
        return False
    for keyword in CRYPTO_NEWS_KEYWORDS:
        if re.fullmatch(r"[a-z0-9]+", keyword) and len(keyword) <= 4:
            if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text):
                return True
        elif keyword in text:
            return True
    return False


def _news_title_key(title: str) -> str:
    text = re.sub(r"\s+-\s+[^-]{2,80}$", "", str(title or ""))
    text = re.sub(r"[^0-9a-zA-Z가-힣]+", " ", text).strip().lower()
    return text


def _coin_news_payload(limit: int = 70, force: bool = False) -> dict:
    global _coin_news_cache
    now = time.time()
    if not force and _coin_news_cache and now - _coin_news_cache[0] < COIN_NEWS_CACHE_SECONDS:
        cached = _coin_news_cache[1]
        return {**cached, "items": list(cached.get("items") or [])[:limit], "cached": True}

    items: list[dict] = []
    sources: list[dict] = []
    errors: list[dict] = []
    seen_links: set[str] = set()
    seen_titles: set[str] = set()
    fetched: dict[str, tuple[list[dict], str | None]] = {}
    with ThreadPoolExecutor(max_workers=min(COIN_NEWS_FEED_WORKERS, len(COIN_NEWS_FEEDS))) as pool:
        future_map = {
            pool.submit(_rss_items, source, int(source.get("limit", 24))): source
            for source in COIN_NEWS_FEEDS
        }
        for future in as_completed(future_map):
            source = future_map[future]
            try:
                fetched[source["name"]] = future.result()
            except Exception as exc:  # noqa: BLE001
                fetched[source["name"]] = ([], str(exc))

    for source in COIN_NEWS_FEEDS:
        rows, error = fetched.get(source["name"], ([], "뉴스 소스 응답 없음"))
        if error:
            errors.append({"source": source["name"], "error": error})
            sources.append({"name": source["name"], "count": 0, "ok": False})
            continue
        added = 0
        for row in rows:
            if source.get("filter") and not _looks_like_crypto_news(row):
                continue
            link_key = (row.get("link") or "").split("?")[0].strip().lower()
            title_key = _news_title_key(row.get("title") or "")
            if (link_key and link_key in seen_links) or (title_key and title_key in seen_titles):
                continue
            if link_key:
                seen_links.add(link_key)
            if title_key:
                seen_titles.add(title_key)
            items.append(row)
            added += 1
        sources.append({"name": source["name"], "count": added, "ok": True})

    items.sort(key=lambda x: float(x.get("published_ts") or 0), reverse=True)
    latest = items[0] if items else {}
    payload = {
        "items": items,
        "sources": sources,
        "errors": errors,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_title": latest.get("title"),
        "latest_source": latest.get("source"),
        "latest_ts": latest.get("published_ts"),
        "total_count": len(items),
        "cached": False,
    }
    _coin_news_cache = (now, payload)
    return {**payload, "items": items[:limit]}


def _compact_news_for_ai(items: list[dict], limit: int = 30) -> list[dict]:
    rows = []
    for item in items[:limit]:
        rows.append({
            "source": item.get("source"),
            "publisher": item.get("publisher"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "published": item.get("published"),
        })
    return rows


def _fallback_news_summary(items: list[dict], reason: str | None = None) -> dict:
    joined = " ".join(f"{i.get('title') or ''} {i.get('summary') or ''}" for i in items).lower()
    asset_hits = []
    for label, keys in [
        ("BTC/비트코인", ("btc", "bitcoin", "비트코인")),
        ("ETH/이더리움", ("eth", "ethereum", "이더리움")),
        ("XRP/리플", ("xrp", "ripple", "리플")),
        ("SOL/솔라나", ("solana", "솔라나")),
        ("스테이블코인", ("stablecoin", "스테이블코인", "테더", "usdt")),
    ]:
        if any(k in joined for k in keys):
            asset_hits.append(label)

    negative = sum(joined.count(k) for k in ("하락", "약세", "유출", "리스크", "규제", "bear", "sell"))
    positive = sum(joined.count(k) for k in ("상승", "강세", "유입", "반등", "bull", "rally"))
    mood = "혼조"
    if negative > positive + 2:
        mood = "약세/경계"
    elif positive > negative + 2:
        mood = "강세/반등"

    headlines = [i.get("title") for i in items if i.get("title")]
    return {
        "headline": headlines[0] if headlines else "최신 코인 뉴스를 불러왔습니다.",
        "market_mood": mood,
        "brief": headlines[:4] or ["요약할 뉴스가 아직 없습니다."],
        "key_assets": asset_hits[:6] or ["BTC/비트코인"],
        "risks": [
            "무료 공개 뉴스 피드 기준이라 일부 기사 반영은 소스 갱신 속도에 따라 지연될 수 있습니다.",
            "헤드라인 중심 요약이므로 기사 원문 확인이 필요합니다.",
        ],
        "watch": [
            "비트코인 현물 ETF 자금 흐름",
            "달러/금리 관련 매크로 뉴스",
            "주요 거래소·규제 당국 발언",
        ],
        "source_note": f"최신 뉴스 {len(items)}건 기준",
        "fallback": True,
        "ai_error": reason,
    }


def _ai_provider_order() -> list[str]:
    preferred = (config.AI_PROVIDER or "mock").lower()
    candidates = [preferred]
    if _usable_ai_key(config.OPENAI_API_KEY):
        candidates.append("openai")
    if _usable_ai_key(config.ANTHROPIC_API_KEY):
        candidates.append("claude")
    if _usable_ai_key(config.GEMINI_API_KEY):
        candidates.append("gemini")
    out = []
    for name in candidates:
        if name not in out:
            out.append(name)
    return out


def _coin_news_summary_payload(force: bool = False, news_limit: int = 30) -> dict:
    global _coin_news_summary_cache
    now = time.time()
    if not force and _coin_news_summary_cache and now - _coin_news_summary_cache[0] < COIN_NEWS_SUMMARY_CACHE_SECONDS:
        return {**_coin_news_summary_cache[1], "cached": True}

    news_payload = _coin_news_payload(limit=120, force=force)
    items = list(news_payload.get("items") or [])[:news_limit]
    base = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": config.AI_PROVIDER,
        "model": config.MODELS.get(config.AI_PROVIDER, "?"),
        "news_count": len(items),
        "sources": news_payload.get("sources") or [],
        "latest_news_ts": (items[0] or {}).get("published_ts") if items else None,
        "cached": False,
    }
    if not items:
        payload = {**base, "summary": _fallback_news_summary(items, "요약할 뉴스가 없습니다.")}
        _coin_news_summary_cache = (now, payload)
        return payload

    user_payload = {
        "generated_at": base["generated_at"],
        "news_count": len(items),
        "news": _compact_news_for_ai(items, news_limit),
    }
    user_content = "다음 최신 암호화폐 뉴스 목록을 한국어로 요약하라:\n" + json.dumps(
        user_payload, ensure_ascii=False, indent=2
    )

    provider_used = None
    ai_errors = []
    summary = None
    for provider_name in _ai_provider_order():
        try:
            provider = get_provider(provider_name)
            summary = provider.decide(COIN_NEWS_SUMMARY_SYSTEM, user_content, COIN_NEWS_SUMMARY_SCHEMA)
            provider_used = provider_name
            summary["fallback"] = False
            summary["ai_error"] = None
            break
        except Exception as e:  # noqa: BLE001
            ai_errors.append(f"{provider_name}: {type(e).__name__}: {e}")
            continue
    if summary is None:
        summary = _fallback_news_summary(items, " | ".join(ai_errors))
        provider_used = "fallback"

    payload = {
        **base,
        "provider": provider_used,
        "model": config.MODELS.get(provider_used, "?") if provider_used != "fallback" else "fallback",
        "summary": summary,
        "ai_errors": ai_errors if summary.get("fallback") else [],
    }
    _coin_news_summary_cache = (now, payload)
    return payload


def _portfolio_payload() -> dict:
    snapshot = store.snapshot()
    portfolio = snapshot.get("portfolio") or {}
    items = list(portfolio.get("items") or [])
    manual_items = db.manual_portfolio_items()
    account_error = None

    if not items:
        try:
            portfolio = _broker().get_portfolio()
            items = list(portfolio.get("items") or [])
        except Exception as e:  # noqa: BLE001
            account_error = str(e)
            portfolio = {}
            items = []

    if not items and manual_items:
        tickers = [
            m["ticker"]
            for m in manual_items
            if m.get("ticker") and m.get("ticker") != "KRW" and float(m.get("balance") or 0) > 0
        ]
        prices = _current_prices(tickers)
        for m in manual_items:
            ticker = m.get("ticker") or ""
            balance = float(m.get("balance") or 0)
            avg_price = float(m.get("avg_buy_price") or 0)
            if balance <= 0:
                continue
            if ticker == "KRW":
                current_value = balance
                principal = balance
                current_price = 1.0
                return_pct = 0.0
            else:
                current_price = prices.get(ticker, avg_price)
                principal = balance * avg_price
                current_value = balance * current_price
                return_pct = ((current_value / principal) - 1) * 100 if principal > 0 else 0.0
            items.append({
                "currency": m.get("currency") or ("KRW" if ticker == "KRW" else ticker.replace("KRW-", "")),
                "ticker": ticker,
                "balance": balance,
                "avg_buy_price": avg_price,
                "principal": principal,
                "current_price": current_price,
                "current_value": current_value,
                "return_pct": return_pct,
            })
        portfolio = {
            "total_principal": sum(float(i.get("principal") or 0) for i in items),
            "total_value": sum(float(i.get("current_value") or 0) for i in items),
        }

    if not items:
        positions = [p for p in db.all_positions() if float(p.get("volume") or 0) > 0]
        tickers = [p["ticker"] for p in positions if p.get("ticker")]
        prices = _current_prices(tickers)
        for p in positions:
            ticker = p["ticker"]
            volume = float(p.get("volume") or 0)
            avg_price = float(p.get("avg_price") or 0)
            current_price = prices.get(ticker, avg_price)
            principal = volume * avg_price
            current_value = volume * current_price
            items.append({
                "currency": ticker.replace("KRW-", ""),
                "ticker": ticker,
                "balance": volume,
                "avg_buy_price": avg_price,
                "principal": principal,
                "current_price": current_price,
                "current_value": current_value,
                "return_pct": ((current_value / principal) - 1) * 100 if principal > 0 else 0,
            })
        portfolio = {
            "total_principal": sum(float(i.get("principal") or 0) for i in items),
            "total_value": sum(float(i.get("current_value") or 0) for i in items),
        }

    total_value = float(portfolio.get("total_value") or sum(float(i.get("current_value") or 0) for i in items))
    total_principal = float(portfolio.get("total_principal") or sum(float(i.get("principal") or 0) for i in items))
    stats = db.trade_stats_by_ticker()
    holdings = []
    cash_value = 0.0
    unrealized_total = 0.0

    for item in items:
        currency = item.get("currency") or ""
        ticker = item.get("ticker") or ("KRW" if currency == "KRW" else f"KRW-{currency}")
        current_value = float(item.get("current_value") or 0)
        principal = float(item.get("principal") or (current_value if currency == "KRW" else 0))
        unrealized = 0.0 if currency == "KRW" else current_value - principal
        weight = float(item.get("weight") or ((current_value / total_value * 100) if total_value > 0 else 0))
        stat = stats.get(ticker, {})
        if currency == "KRW":
            cash_value += current_value
        unrealized_total += unrealized
        holdings.append({
            **item,
            "ticker": ticker,
            "principal": principal,
            "current_value": current_value,
            "unrealized_pnl": unrealized,
            "realized_pnl": float(stat.get("realized_pnl") or 0),
            "trades_count": int(stat.get("trades_count") or 0),
            "buy_count": int(stat.get("buy_count") or 0),
            "sell_count": int(stat.get("sell_count") or 0),
            "last_trade_at": stat.get("last_trade_at"),
            "weight": weight,
        })

    holdings.sort(key=lambda x: float(x.get("current_value") or 0), reverse=True)
    realized_total = db.total_realized_pnl()
    coin_value = max(0.0, total_value - cash_value)
    largest = max(holdings, key=lambda x: float(x.get("weight") or 0), default=None)
    return {
        "summary": {
            "total_principal": total_principal,
            "total_value": total_value,
            "cash_value": cash_value,
            "coin_value": coin_value,
            "cash_ratio": (cash_value / total_value * 100) if total_value > 0 else 0,
            "unrealized_pnl": unrealized_total,
            "realized_pnl": realized_total,
            "total_pnl": realized_total + unrealized_total,
            "total_return_pct": ((total_value / total_principal) - 1) * 100 if total_principal > 0 else 0,
            "assets_count": len([h for h in holdings if h.get("currency") != "KRW"]),
            "largest_asset": largest.get("currency") if largest else None,
            "largest_weight": float(largest.get("weight") or 0) if largest else 0,
        },
        "holdings": holdings,
        "manual_items": manual_items,
        "account_error": account_error,
        "daily": db.get_daily_pnl(days=30),
        "risk": {
            "max_order_krw": config.MAX_ORDER_KRW,
            "max_daily_loss_krw": config.MAX_DAILY_LOSS_KRW,
            "min_confidence": config.MIN_CONFIDENCE,
        },
    }


def _normalize_manual_ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker:
        raise ValueError("종목을 입력하세요")
    if ticker == "KRW":
        return ticker
    if "-" not in ticker:
        ticker = f"KRW-{ticker}"
    if not ticker.startswith("KRW-") or len(ticker) <= 4:
        raise ValueError("종목은 KRW-BTC 또는 BTC 형식으로 입력하세요")
    return ticker


def _non_negative_float(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 숫자를 입력하세요") from exc
    if number < 0:
        raise ValueError(f"{label}은 0 이상이어야 합니다")
    return number


def _normalize_trade_ticker(value: str) -> str:
    ticker = _normalize_manual_ticker(value)
    if ticker == "KRW":
        raise ValueError("거래 종목은 KRW-BTC 같은 코인 마켓이어야 합니다")
    markets = {m["market"] for m in _coin_markets()}
    if ticker not in markets:
        raise ValueError(f"업비트 KRW 마켓에 없는 종목입니다: {ticker}")
    return ticker


def _price_now(ticker: str) -> float:
    price = _current_prices([ticker]).get(ticker)
    if price is None:
        raw = pyupbit.get_current_price(ticker)
        price = float(raw) if raw is not None else None
    if price is None or price <= 0:
        raise ValueError(f"{ticker} 현재가 조회 실패")
    return float(price)


def _exchange_error(result) -> str | None:
    if isinstance(result, dict) and result.get("error"):
        err = result.get("error") or {}
        if isinstance(err, dict):
            return f"{err.get('name') or 'exchange_error'}: {err.get('message') or err}"
        return str(err)
    return None


def _manual_order_context(payload: dict, *, execute: bool) -> dict:
    side = str(payload.get("side") or "").lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side는 buy 또는 sell이어야 합니다")
    ticker = _normalize_trade_ticker(payload.get("ticker") or "")
    price = _price_now(ticker)
    broker = _broker()
    dry_run = bool(config.DRY_RUN or broker.client is None)

    krw_balance = 0.0
    coin_balance = 0.0
    avg_buy_price = 0.0
    try:
        balances = broker.get_balances()
        krw_balance = broker.krw_from_balances(balances)
        coin_balance, avg_buy_price = broker.position_from_balances(ticker, balances)
    except Exception as exc:  # noqa: BLE001
        if not dry_run:
            raise ValueError(f"업비트 잔고 조회 실패: {exc}") from exc

    confirm = str(payload.get("confirm") or "").strip().upper()
    if execute and not dry_run and confirm != "LIVE":
        raise ValueError("실거래 주문은 확인 입력란에 LIVE를 입력해야 합니다")

    krw_amount = 0.0
    volume = 0.0
    percent = None
    if side == "buy":
        krw_amount = _non_negative_float(payload.get("krw_amount"), "매수금액")
        if krw_amount < config.MIN_ORDER_KRW:
            raise ValueError(f"매수금액은 최소 {config.MIN_ORDER_KRW:,}원 이상이어야 합니다")
        if not dry_run and krw_balance < krw_amount:
            raise ValueError(f"원화 잔고 부족: {krw_balance:,.0f}원")
        volume = krw_amount / price
    else:
        raw_volume = payload.get("volume")
        raw_percent = payload.get("percent")
        if raw_volume not in {None, ""}:
            volume = _non_negative_float(raw_volume, "매도수량")
        elif raw_percent not in {None, ""}:
            percent = _non_negative_float(raw_percent, "매도비율")
            if percent <= 0 or percent > 100:
                raise ValueError("매도비율은 0 초과 100 이하로 입력하세요")
            if coin_balance <= 0:
                raise ValueError("보유 수량이 없어 비율 매도를 계산할 수 없습니다")
            volume = coin_balance * percent / 100
        else:
            raise ValueError("매도수량 또는 매도비율을 입력하세요")
        krw_amount = volume * price
        if volume <= 0:
            raise ValueError("매도수량은 0보다 커야 합니다")
        if krw_amount < config.MIN_ORDER_KRW:
            raise ValueError(f"예상 매도금액은 최소 {config.MIN_ORDER_KRW:,}원 이상이어야 합니다")
        if not dry_run and coin_balance < volume:
            raise ValueError(f"보유 수량 부족: {coin_balance:.8f}")

    return {
        "ticker": ticker,
        "side": side,
        "price": price,
        "krw_amount": krw_amount,
        "volume": volume,
        "percent": percent,
        "dry_run": dry_run,
        "live": not dry_run,
        "krw_balance": krw_balance,
        "coin_balance": coin_balance,
        "avg_buy_price": avg_buy_price,
        "estimated_fee": krw_amount * 0.0005,
        "min_order_krw": config.MIN_ORDER_KRW,
    }


# ============ JSON API ============
@app.route("/api/state")
def api_state():
    return jsonify(store.snapshot())


@app.route("/healthz")
def healthz():
    db_ok = True
    error = None
    try:
        db.recent_decisions(limit=1)
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        error = str(exc)

    snapshot = store.snapshot()
    payload = {
        "status": "ok" if db_ok else "error",
        "db_ok": db_ok,
        "loop_expected": config.RUN_TRADING_LOOP,
        "loop_running": bool(snapshot.get("loop_running")),
        "last_update": snapshot.get("last_update"),
        "cycle_count": snapshot.get("cycle_count"),
        "error": error or snapshot.get("error"),
    }
    return jsonify(payload), 200 if db_ok else 503


@app.route("/readyz")
def readyz():
    snapshot = store.snapshot()
    loop_ready = (not config.RUN_TRADING_LOOP) or bool(snapshot.get("loop_running"))
    payload = {
        "ready": loop_ready,
        "loop_expected": config.RUN_TRADING_LOOP,
        "loop_running": bool(snapshot.get("loop_running")),
        "last_update": snapshot.get("last_update"),
    }
    return jsonify(payload), 200 if loop_ready else 503


@app.route("/api/config")
def api_config():
    return jsonify({
        "dry_run": config.DRY_RUN,
        "allow_live_trading": config.ALLOW_LIVE_TRADING,
        "provider": config.AI_PROVIDER,
        "model": config.MODELS.get(config.AI_PROVIDER, "?"),
        "tickers": config.TICKERS,
        "coin_markets": _coin_markets(),
        "intervals": [
            {"value": "minute15", "label": "15분"},
            {"value": "minute60", "label": "1시간"},
            {"value": "day", "label": "일봉"},
        ],
        "risk": {
            "max_order_krw": config.MAX_ORDER_KRW,
            "min_order_krw": config.MIN_ORDER_KRW,
            "max_daily_loss_krw": config.MAX_DAILY_LOSS_KRW,
            "min_confidence": config.MIN_CONFIDENCE,
            "cycle_seconds": config.INTERVAL_SECONDS,
        },
    })


@app.route("/api/control", methods=["POST"])
def api_control():
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action", "")).lower()
    if action == "pause":
        store.set_paused(True)
    elif action == "resume":
        store.set_paused(False)
    else:
        return jsonify({"error": "지원하지 않는 action입니다"}), 400
    return jsonify(store.snapshot())


@app.route("/api/pnl")
def api_pnl():
    trades = db.recent_trades(limit=50)
    closed = [t for t in trades if t["side"] == "sell" and (t.get("realized_pnl") or 0) != 0]
    wins = sum(1 for t in closed if (t.get("realized_pnl") or 0) > 0)
    win_rate = (wins / len(closed) * 100) if closed else 0.0
    daily = db.get_daily_pnl(days=30)
    today_trades = daily[0]["trades_count"] if daily else 0
    return jsonify({
        "today": db.get_today_realized_pnl(),
        "total": db.total_realized_pnl(),
        "today_trades": today_trades,
        "win_rate": win_rate,
        "daily": daily,
        "trades": trades,
    })


@app.route("/api/portfolio")
def api_portfolio():
    global _portfolio_cache
    now = time.time()
    if _portfolio_cache and now - _portfolio_cache[0] < PORTFOLIO_CACHE_SECONDS:
        return jsonify({**_portfolio_cache[1], "cached": True})
    payload = _portfolio_payload()
    _portfolio_cache = (now, payload)
    return jsonify(payload)


@app.route("/api/manual_portfolio", methods=["GET", "POST", "DELETE"])
def api_manual_portfolio():
    global _portfolio_cache
    if request.method == "GET":
        return jsonify({"items": db.manual_portfolio_items()})

    payload = request.get_json(silent=True) or {}
    ticker_value = payload.get("ticker") or request.args.get("ticker")
    try:
        ticker = _normalize_manual_ticker(ticker_value)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if request.method == "DELETE":
        deleted = db.delete_manual_portfolio_item(ticker)
        _portfolio_cache = None
        return jsonify({"deleted": deleted, "portfolio": _portfolio_payload()})

    try:
        balance = _non_negative_float(payload.get("balance"), "수량/원화")
        avg_buy_price = 1.0 if ticker == "KRW" else _non_negative_float(payload.get("avg_buy_price"), "평단")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    item = db.upsert_manual_portfolio_item(ticker, balance, avg_buy_price)
    _portfolio_cache = None
    return jsonify({"item": item, "portfolio": _portfolio_payload()})


@app.route("/api/manual_order/preview", methods=["POST"])
def api_manual_order_preview():
    payload = request.get_json(silent=True) or {}
    try:
        ctx = _manual_order_context(payload, execute=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "preview": ctx})


@app.route("/api/manual_order", methods=["POST"])
def api_manual_order():
    global _portfolio_cache
    payload = request.get_json(silent=True) or {}
    try:
        ctx = _manual_order_context(payload, execute=True)
        broker = _broker()
        if ctx["side"] == "buy":
            result = broker.buy(ctx["ticker"], ctx["krw_amount"])
        else:
            result = broker.sell(ctx["ticker"], ctx["volume"])
        error = _exchange_error(result)
        if error:
            return jsonify({"error": error, "result": result}), 400
        trade = db.record_trade(
            ticker=ctx["ticker"],
            side=ctx["side"],
            price=ctx["price"],
            volume=ctx["volume"],
            krw_amount=ctx["krw_amount"],
            dry_run=ctx["dry_run"],
            raw_result=json.dumps(result, ensure_ascii=False, default=str),
        )
        _portfolio_cache = None
        try:
            store.set_pnl(db.get_today_realized_pnl(), db.total_realized_pnl())
        except Exception:  # noqa: BLE001
            pass
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "order": ctx,
        "trade": trade,
        "result": result,
        "portfolio": _portfolio_payload(),
    })


@app.route("/api/analyze_context")
def api_analyze_context():
    ticker = request.args.get("ticker", "KRW-BTC").upper()
    portfolio = _portfolio_payload()
    holding = next((h for h in portfolio["holdings"] if h.get("ticker") == ticker), None)
    return jsonify({
        "ticker": ticker,
        "holding": holding,
        "position": db.get_position(ticker),
        "decisions": _compact_reasoning_rows(db.recent_decisions(limit=8, ticker=ticker)),
        "trades": db.recent_trades(limit=8, ticker=ticker),
        "portfolio_summary": portfolio["summary"],
    })


@app.route("/api/decisions")
def api_decisions():
    limit = _int_arg("limit", 100, 1, 500)
    ticker = request.args.get("ticker", "").upper() or None
    action = request.args.get("action", "").upper() or None
    if action and action not in {"BUY", "SELL", "HOLD"}:
        action = None
    rows = db.recent_decisions(limit=limit, ticker=ticker, action=action)
    return jsonify({"items": _compact_reasoning_rows(rows)})


@app.route("/api/trades")
def api_trades():
    limit = _int_arg("limit", 100, 1, 500)
    ticker = request.args.get("ticker", "").upper() or None
    side = request.args.get("side", "").lower() or None
    if side and side not in {"buy", "sell"}:
        side = None
    return jsonify({"items": db.recent_trades(limit=limit, ticker=ticker, side=side)})


@app.route("/api/export/decisions.csv")
def api_export_decisions():
    rows = db.recent_decisions(limit=_int_arg("limit", 500, 1, 5000))
    fields = [
        "id", "ts", "ticker", "price", "rsi", "trend", "change_pct",
        "action", "confidence", "reasoning", "order_side", "order_reason",
    ]
    return _csv_response("stockagent-decisions.csv", rows, fields)


@app.route("/api/export/trades.csv")
def api_export_trades():
    rows = db.recent_trades(limit=_int_arg("limit", 500, 1, 5000))
    fields = [
        "id", "ts", "ticker", "side", "price", "volume", "krw_amount",
        "fee", "realized_pnl", "dry_run", "raw_result",
    ]
    return _csv_response("stockagent-trades.csv", rows, fields)


@app.route("/api/candles")
def api_candles():
    ticker = request.args.get("ticker", "KRW-BTC")
    interval = request.args.get("interval", config.CANDLE_INTERVAL)
    count = int(request.args.get("count", 120))
    try:
        return jsonify(_coin_candles_payload(ticker, interval, count))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400


@app.route("/api/coin/orderbook")
def api_coin_orderbook():
    ticker = request.args.get("ticker", "KRW-BTC").upper()
    now = time.time()
    cached = _coin_orderbook_cache.get(ticker)
    if cached and now - cached[0] < COIN_ORDERBOOK_CACHE_SECONDS:
        return jsonify({**cached[1], "cached": True})
    try:
        orderbook = pyupbit.get_orderbook(ticker)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    if isinstance(orderbook, list):
        orderbook = orderbook[0] if orderbook else {}
    if not orderbook:
        return jsonify({"error": f"{ticker} 호가 조회 실패"}), 400
    payload = {
        "ticker": ticker,
        "timestamp": orderbook.get("timestamp"),
        "total_ask_size": orderbook.get("total_ask_size"),
        "total_bid_size": orderbook.get("total_bid_size"),
        "units": orderbook.get("orderbook_units") or [],
    }
    _coin_orderbook_cache[ticker] = (now, payload)
    return jsonify(payload)


@app.route("/api/coin/quote")
def api_coin_quote():
    ticker = request.args.get("ticker", "KRW-BTC").upper()
    now = time.time()
    cached = _coin_quote_cache.get(ticker)
    if cached and now - cached[0] < COIN_QUOTE_CACHE_SECONDS:
        return jsonify({**cached[1], "cached": True})
    try:
        price = pyupbit.get_current_price(ticker)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    if price is None:
        return jsonify({"error": f"{ticker} 현재가 조회 실패"}), 400
    payload = {
        "ticker": ticker,
        "price": float(price),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _coin_quote_cache[ticker] = (now, payload)
    return jsonify(payload)


@app.route("/api/coin/mini_charts")
def api_coin_mini_charts():
    raw_tickers = request.args.get("tickers", "")
    requested = [
        t.strip().upper()
        for t in raw_tickers.split(",")
        if t.strip().upper().startswith("KRW-")
    ]
    markets = {m["market"]: m for m in _coin_markets()}
    tickers = [ticker for ticker in requested if ticker in markets][:24]
    if not tickers:
        tickers = list(markets.keys())[:18]

    interval = request.args.get("interval", "minute60")
    if interval not in {"minute1", "minute3", "minute5", "minute10", "minute15", "minute30", "minute60", "day"}:
        interval = "minute60"
    count = _int_arg("count", 36, 16, 80)
    force = request.args.get("refresh") in {"1", "true", "yes"}
    cache_key = (",".join(tickers), interval, count)
    now = time.time()
    cached = _coin_mini_charts_cache.get(cache_key)
    if not force and cached and now - cached[0] < COIN_MINI_CHART_CACHE_SECONDS:
        return jsonify({**cached[1], "cached": True})

    prices = _current_prices(tickers)

    def one(ticker: str) -> dict:
        meta = markets.get(ticker, {})
        try:
            closes: list[float] = []
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    closes = _upbit_candle_closes(ticker, interval, count)
                    last_error = None
                except Exception as exc:  # noqa: BLE001
                    closes = []
                    last_error = exc
                if closes:
                    break
                time.sleep(0.45 + attempt * 0.35)
            if not closes:
                df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
                if df is not None and not df.empty:
                    closes = [float(v) for v in df["close"].tolist()]
            if len(closes) < 2:
                raise RuntimeError(str(last_error) if last_error else "캔들 없음")
            live_price = prices.get(ticker)
            if live_price is not None and closes:
                closes[-1] = float(live_price)
            change_pct = (closes[-1] / closes[0] - 1) * 100 if closes and closes[0] else 0.0
            return {
                "ticker": ticker,
                "symbol": meta.get("symbol") or ticker.replace("KRW-", ""),
                "korean_name": meta.get("korean_name") or ticker.replace("KRW-", ""),
                "english_name": meta.get("english_name") or ticker.replace("KRW-", ""),
                "price": closes[-1] if closes else None,
                "change_pct": change_pct,
                "closes": closes,
                "ok": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ticker": ticker,
                "symbol": meta.get("symbol") or ticker.replace("KRW-", ""),
                "korean_name": meta.get("korean_name") or ticker.replace("KRW-", ""),
                "english_name": meta.get("english_name") or ticker.replace("KRW-", ""),
                "price": prices.get(ticker),
                "change_pct": None,
                "closes": [],
                "ok": False,
                "error": str(exc),
            }

    results_by_ticker: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as pool:
        future_map = {pool.submit(one, ticker): ticker for ticker in tickers}
        for future in as_completed(future_map):
            ticker = future_map[future]
            try:
                results_by_ticker[ticker] = future.result()
            except Exception as exc:  # noqa: BLE001
                results_by_ticker[ticker] = {"ticker": ticker, "ok": False, "error": str(exc), "closes": []}

    payload = {
        "items": [results_by_ticker[ticker] for ticker in tickers if ticker in results_by_ticker],
        "interval": interval,
        "count": count,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }
    _coin_mini_charts_cache[cache_key] = (now, payload)
    return jsonify(payload)


@app.route("/api/coin/news")
def api_coin_news():
    limit = _int_arg("limit", COIN_NEWS_DEFAULT_LIMIT, 10, 120)
    force = request.args.get("refresh") in {"1", "true", "yes"}
    return jsonify(_coin_news_payload(limit=limit, force=force))


@app.route("/api/coin/news_summary")
def api_coin_news_summary():
    force = request.args.get("refresh") in {"1", "true", "yes"}
    news_limit = _int_arg("news_limit", 30, 10, 60)
    return jsonify(_coin_news_summary_payload(force=force, news_limit=news_limit))


@app.route("/api/stocks/candles")
def api_stock_candles():
    try:
        code = _stock_code(request.args.get("code", "005930"))
        timeframe = request.args.get("timeframe", "day")
        count = _int_arg("count", 120, 30, 300)
        payload = _stock_chart(code, timeframe, count)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    if not payload["closes"]:
        return jsonify({"error": f"{code} 주식 차트 조회 실패"}), 400
    return jsonify(payload)


@app.route("/api/stocks/quote")
def api_stock_quote():
    try:
        code = _stock_code(request.args.get("code", "005930"))
        payload = _stock_quote(code)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    return jsonify(payload)


@app.route("/api/ticker_quotes")
def api_ticker_quotes():
    """헤더 티커 테이프용. 보유 종목 + 주요 코인 시세."""
    global _ticker_quotes_cache
    now = time.time()
    if _ticker_quotes_cache and now - _ticker_quotes_cache[0] < TICKER_QUOTES_CACHE_SECONDS:
        return jsonify({**_ticker_quotes_cache[1], "cached": True})
    base = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE", "KRW-ADA"]
    items = []
    try:
        prices = pyupbit.get_current_price(base)
        if not isinstance(prices, dict):
            prices = {base[0]: prices}
        for t in base:
            try:
                df = pyupbit.get_ohlcv(t, interval="day", count=2)
                if df is None or len(df) < 2:
                    continue
                prev_close = float(df["close"].iloc[-2])
                cur = float(prices.get(t) or df["close"].iloc[-1])
                chg = (cur / prev_close - 1) * 100 if prev_close else 0
                items.append({
                    "sym": t.replace("KRW-", ""),
                    "price": cur,
                    "chg_pct": chg,
                })
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        return jsonify({"items": [], "error": str(e)})
    payload = {"items": items}
    _ticker_quotes_cache = (now, payload)
    return jsonify(payload)


@app.route("/api/analyze")
def api_analyze():
    ticker = request.args.get("ticker", "KRW-BTC").upper()
    interval = request.args.get("interval", config.CANDLE_INTERVAL)
    count = _int_arg("count", 120, 30, 300)
    cache_key = (ticker, interval, count)
    cached = _analysis_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < ANALYSIS_CACHE_SECONDS:
        return jsonify({**cached[1], "cached": True})
    try:
        snapshot = _broker().market_snapshot(ticker, interval=interval, count=count)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 400
    report = analyze(snapshot)
    payload = {"snapshot": snapshot, "report": report, "cached": False}
    _analysis_cache[cache_key] = (now, payload)
    return jsonify(payload)


# ============ HTML 페이지 ============
@app.route("/")
@app.route("/coin")
def coin_page():
    return _coin_page_html()


@app.route("/stocks")
def stocks_page():
    return STOCKS_HTML


@app.route("/assets")
def assets_page():
    return DASHBOARD_HTML


@app.route("/analyze")
def analyze_page():
    return ANALYZE_HTML


# 디자인 시스템 공통 (CSS + 헤더 + 공유 JS)
BASE_CSS = """
:root { color-scheme: dark; }
html,body { margin:0; background:#070a0e; }
* { box-sizing: border-box; }
body { font-family:'JetBrains Mono', ui-monospace, monospace; color:#e6ebf2; padding-bottom:60px; }
::-webkit-scrollbar { width:9px; height:9px; }
::-webkit-scrollbar-thumb { background:#1c2430; border-radius:6px; }
::-webkit-scrollbar-track { background:#0a0e14; }
@keyframes lp { 0%,100%{opacity:1} 50%{opacity:.25} }
@keyframes tape { from{transform:translateX(0)} to{transform:translateX(-50%)} }

.hd { position:sticky; top:0; z-index:20; background:#0d1219; border-bottom:1px solid #1c2430; }
.hd-row { max-width:1340px; margin:0 auto; display:flex; align-items:center; gap:12px; padding:13px 22px; }
.brand { font-weight:800; font-size:16px; letter-spacing:.5px; }
.brand .cursor { color:#1fd6a8; }
.brand.dry .cursor { color:#e0b341; }
.pill-live { display:inline-flex; align-items:center; gap:6px; font-size:11px;
             color:#ff5d6c; background:rgba(58,31,35,.4); border:1px solid #3a1f23;
             padding:3px 9px; border-radius:3px; font-weight:600; }
.pill-live .dot { width:6px; height:6px; border-radius:50%; background:#ff5d6c; animation:lp 1.4s infinite; }
.pill-dry { display:inline-flex; align-items:center; gap:6px; font-size:11px;
            color:#e0b341; background:rgba(58,46,15,.4); border:1px solid #3a311f;
            padding:3px 9px; border-radius:3px; font-weight:600; }
.pill-dry .dot { width:6px; height:6px; border-radius:50%; background:#e0b341; animation:lp 1.4s infinite; }
.pill-ai { font-size:11px; color:#8a95a8; border:1px solid #1c2430; padding:3px 9px; border-radius:3px; }
.pill-bot { font-size:11px; color:#8a95a8; border:1px solid #1c2430; padding:3px 9px; border-radius:3px; }
.pill-bot.on { color:#1fd6a8; border-color:#1c3a32; background:rgba(31,214,168,.08); }
.pill-bot.pause { color:#e0b341; border-color:#3a311f; background:rgba(224,179,65,.08); }
.mini-btn, .terminal-select { font-family:'JetBrains Mono',monospace; font-size:11px; border-radius:3px;
            border:1px solid #1c2430; background:#0a0e14; color:#8a95a8; padding:4px 8px; }
.mini-btn { cursor:pointer; text-decoration:none; }
.mini-btn:hover { color:#e6ebf2; border-color:#3a4658; }
.mini-btn.danger { color:#e0b341; border-color:#3a311f; background:rgba(224,179,65,.08); }
.mini-btn.on { color:#e6ebf2; background:#1c2430; font-weight:600; }
.terminal-select { outline:none; }
.terminal-select:focus { border-color:#1fd6a8; }
.head-tools, .section-line, .inline-tools, .chart-controls { display:flex; align-items:center; gap:6px; }
.section-line { justify-content:space-between; margin:0 0 12px; }
.section-line .section-title { margin:0; }
.inline-tools { flex-wrap:wrap; }
.tabs { display:flex; gap:4px; background:#0a0e14; border:1px solid #1c2430; border-radius:5px; padding:3px; }
.tab { font-family:'JetBrains Mono',monospace; font-size:12px; padding:6px 14px;
       border-radius:4px; border:none; cursor:pointer; background:transparent; color:#8a95a8; text-decoration:none; }
.tab.on { background:#1c2430; color:#e6ebf2; font-weight:600; }
.upd { font-size:11px; color:#5a6577; margin-left:6px; }

.tape-wrap { overflow:hidden; border-top:1px solid #141a23; background:#0a0e14; }
.tape-row { display:flex; width:max-content; animation:tape 60s linear infinite; }
.tape-cell { display:inline-flex; align-items:baseline; gap:7px; padding:7px 18px;
             border-right:1px solid #141a23; font-size:11.5px; white-space:nowrap; }
.tape-cell .sym { color:#8a95a8; }
.tape-cell .px  { color:#cdd5e0; }

.wrap { max-width:1340px; margin:0 auto; padding:22px; }
.section-title { font-size:11px; color:#e0b341; letter-spacing:.08em;
                 text-transform:uppercase; margin:2px 0 12px; }
.kpi-grid { display:grid; grid-template-columns:repeat(6,1fr); border:1px solid #1c2430;
            border-radius:4px; overflow:hidden; margin-bottom:24px; }
.kpi { padding:14px 16px; border-right:1px solid #141a23; background:#0d1219; }
.kpi:last-child { border-right:none; }
.kpi .label { font-size:10px; color:#e0b341; letter-spacing:.06em;
              text-transform:uppercase; margin-bottom:9px; }
.kpi .val { font-size:19px; font-weight:700; color:#e6ebf2; }
.up { color:#1fd6a8 !important; }
.down { color:#ff5d6c !important; }
.muted { color:#5a6577; }

.box { border:1px solid #1c2430; border-radius:4px; overflow:hidden; }
.box-head { display:flex; align-items:center; gap:14px; padding:10px 15px; background:#0d1219;
            border-bottom:1px solid #1c2430; font-size:11px; color:#e0b341;
            letter-spacing:.06em; text-transform:uppercase; }
.box-head .total { color:#5a6577; margin-left:auto; }
.legend { display:inline-flex; align-items:center; gap:5px; color:#8a95a8; font-weight:400; }
.legend .swatch { width:14px; height:2px; display:inline-block; }

.row-grid { display:grid; grid-template-columns:1fr 1.4fr; gap:18px; margin-bottom:24px; }
.market-grid { display:grid; grid-template-columns:1.45fr .85fr; gap:18px; margin-bottom:24px; align-items:start; }
.market-actions { display:flex; gap:7px; align-items:center; flex-wrap:wrap; }
.market-actions input { width:120px; font-family:'JetBrains Mono',monospace; background:#0a0e14; color:#e6ebf2;
                        border:1px solid #1c2430; border-radius:3px; padding:5px 8px; font-size:11px; outline:none; }
.market-actions input:focus { border-color:#1fd6a8; }
.market-actions .coin-market-search { width:190px; }
.coin-market-count { color:#5a6577; font-size:10.5px; margin-left:2px; white-space:nowrap; }
.coin-market-directory { border:1px solid #1c2430; border-radius:4px; background:#0a0e14;
                         margin:0 0 18px; overflow:hidden; }
.coin-market-directory-head { display:flex; justify-content:space-between; align-items:center;
                              padding:9px 12px; border-bottom:1px solid #141a23; }
.coin-market-chip-grid { display:grid; grid-template-columns:repeat(8,minmax(0,1fr)); gap:6px;
                         padding:10px 12px; max-height:156px; overflow:auto; }
.coin-market-chip { min-width:0; border:1px solid #1c2430; border-radius:3px; background:#0d1219;
                    color:#8a95a8; padding:7px 8px; cursor:pointer; text-align:left;
                    font-family:'JetBrains Mono',monospace; }
.coin-market-chip:hover { color:#e6ebf2; border-color:#3a4658; }
.coin-market-chip.on { color:#1fd6a8; border-color:#1c3a32; background:rgba(31,214,168,.07); }
.coin-market-chip .sym { display:block; font-size:11px; font-weight:900; color:inherit; }
.coin-market-chip .name { display:block; margin-top:3px; font-size:9.5px; color:#5a6577;
                          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.coin-board-toolbar { margin-top:4px; }
.coin-board-sub { color:#5a6577; font-size:10.5px; margin-top:4px; }
.coin-market-board-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; margin-bottom:10px; }
.coin-mini-card { min-height:154px; border:1px solid #1c2430; border-radius:4px; background:#0d1219;
                  padding:11px 12px; cursor:pointer; overflow:hidden; transition:border-color .15s ease, transform .15s ease;
                  font-family:'JetBrains Mono',monospace; text-align:left; color:inherit; }
.coin-mini-card:hover { border-color:#3a4658; transform:translateY(-1px); }
.coin-mini-card.on { border-color:#1c3a32; box-shadow:0 0 0 1px rgba(31,214,168,.1) inset; }
.coin-mini-head { display:flex; align-items:flex-start; gap:8px; margin-bottom:8px; }
.coin-mini-symbol { color:#e6ebf2; font-size:13px; font-weight:900; line-height:1.1; }
.coin-mini-name { color:#5a6577; font-size:10px; margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.coin-mini-change { margin-left:auto; font-size:11px; font-weight:900; white-space:nowrap; }
.coin-mini-price { color:#cdd5e0; font-size:13px; font-weight:800; margin-bottom:7px; }
.coin-mini-chart { width:100%; height:58px; display:block; }
.coin-mini-note { color:#3a4658; font-size:9.5px; margin-top:7px; }
.market-stat-grid { display:grid; grid-template-columns:repeat(4,1fr); border:1px solid #1c2430;
                    border-radius:4px; overflow:hidden; margin-bottom:20px; }
.market-stat { padding:13px 15px; border-right:1px solid #141a23; background:#0d1219; }
.market-stat:last-child { border-right:none; }
.market-stat .label { font-size:10px; color:#e0b341; letter-spacing:.06em; text-transform:uppercase; margin-bottom:8px; }
.market-stat .val { font-size:18px; font-weight:700; color:#e6ebf2; }
.orderbook-head, .orderbook-row { display:grid; grid-template-columns:1fr 1fr 1fr 1fr; gap:8px; align-items:center; }
.orderbook-row { padding:9px 15px; border-bottom:1px solid #11161f; font-size:11px; }
.orderbook-row:last-child { border-bottom:none; }
.quote-row { display:grid; grid-template-columns:84px 1fr; gap:10px; padding:10px 15px; border-bottom:1px solid #11161f; font-size:11.5px; align-items:center; }
.quote-row:last-child { border-bottom:none; }
.quote-row .k { color:#5a6577; }
.quote-row .v { color:#cdd5e0; text-align:right; font-weight:600; }
.metric-list { display:grid; grid-template-columns:1fr 1fr; border-top:1px solid #141a23; }
.metric-list .quote-row:nth-child(2n-1) { border-right:1px solid #141a23; }
.coin-section-title { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.coin-section-nav { display:inline-flex; gap:3px; background:#0a0e14; border:1px solid #1c2430;
                    border-radius:5px; padding:3px; }
.coin-section-btn { font-family:'JetBrains Mono',monospace; font-size:11px; padding:5px 11px;
                    border-radius:3px; border:none; cursor:pointer; background:transparent; color:#8a95a8; }
.coin-section-btn.on { background:#1c2430; color:#e6ebf2; font-weight:700; }
.coin-section-anchor { scroll-margin-top:132px; }
.coin-panel[hidden] { display:none !important; }
.coin-portfolio-grid { display:grid; grid-template-columns:1.28fr .72fr; gap:18px; margin-bottom:24px; align-items:start; }
.coin-holding-head, .coin-holding-row { display:grid; grid-template-columns:92px 1fr 108px 96px 72px; align-items:center; }
.donut-panel { min-height:236px; display:flex; align-items:center; justify-content:center; padding:18px 12px 10px; background:#0a0e14; }
.donut-svg { width:220px; height:220px; display:block; }
.coin-pf-note { padding:11px 16px; font-size:10.5px; color:#3a4658; line-height:1.5;
                background:#0a0e14; border-top:1px solid #141a23; }
.coin-trade-box { margin-bottom:24px; }
.coin-trade-form { display:grid; grid-template-columns:1.1fr 1fr 1fr 1fr; gap:9px; padding:14px 15px;
                   border-bottom:1px solid #141a23; background:#0a0e14; }
.coin-trade-field { min-width:0; }
.coin-trade-field label { display:block; margin-bottom:6px; color:#5a6577; font-size:10px; letter-spacing:.05em; text-transform:uppercase; }
.coin-trade-input { width:100%; min-width:0; font-family:'JetBrains Mono',monospace; background:#070a0e;
                    color:#e6ebf2; border:1px solid #1c2430; border-radius:3px; padding:7px 9px; font-size:12px; outline:none; }
.coin-trade-input:focus { border-color:#1fd6a8; }
.coin-trade-actions { display:flex; gap:7px; align-items:center; flex-wrap:wrap; padding:12px 15px; background:#0d1219; }
.coin-trade-actions .mini-btn { min-height:30px; }
.coin-trade-actions .buy { color:#1fd6a8; border-color:#1c3a32; background:rgba(31,214,168,.07); }
.coin-trade-actions .sell { color:#ff5d6c; border-color:#3a1f23; background:rgba(255,93,108,.07); }
.coin-trade-status { min-height:36px; padding:0 15px 12px; font-size:11px; color:#5a6577; line-height:1.55; background:#0d1219; }
.coin-trade-status.ok { color:#1fd6a8; }
.coin-trade-status.warn { color:#e0b341; }
.coin-trade-status.err { color:#ff8a93; }
.coin-trade-mode { color:#5a6577; font-size:10px; margin-left:auto; }
.coin-news-grid { display:grid; grid-template-columns:1fr 310px; gap:18px; margin-bottom:24px; align-items:start; }
.news-live-grid { margin-bottom:20px; }
.news-live-grid .val { font-size:16px; }
.news-live-dot { display:inline-block; width:7px; height:7px; margin-right:7px; border-radius:50%;
                 background:#1fd6a8; box-shadow:0 0 9px rgba(31,214,168,.55); animation:lp 1.4s infinite; }
.news-search { min-width:220px; font-family:'JetBrains Mono',monospace; background:#0a0e14; color:#e6ebf2;
               border:1px solid #1c2430; border-radius:3px; padding:5px 8px; font-size:11px; outline:none; }
.news-search:focus { border-color:#1fd6a8; }
.news-item { display:grid; grid-template-columns:88px 1fr 94px; gap:13px; padding:14px 15px;
             border-bottom:1px solid #11161f; align-items:start; }
.news-item:last-child { border-bottom:none; }
.news-source-chip { display:inline-block; color:#e0b341; background:rgba(224,179,65,.08);
                    border:1px solid #3a311f; border-radius:3px; padding:2px 6px; font-size:10px; font-weight:700; }
.news-source-chip.live { color:#1fd6a8; background:rgba(31,214,168,.08); border-color:#1c3a32; margin-top:6px; }
.news-title { color:#e6ebf2; text-decoration:none; font-size:13px; line-height:1.45; font-weight:800; }
.news-title:hover { color:#1fd6a8; }
.news-publisher { color:#5a6577; font-size:10.5px; margin-bottom:5px; }
.news-summary { color:#8a95a8; line-height:1.45; font-size:11px; margin-top:6px; }
.news-time { color:#5a6577; font-size:10.5px; text-align:right; white-space:nowrap; }
.news-source-row { display:grid; grid-template-columns:1fr 54px 38px; gap:8px; align-items:center;
                   padding:11px 15px; border-bottom:1px solid #11161f; font-size:11px; }
.news-source-row:last-child { border-bottom:none; }
.news-source-ok { color:#1fd6a8; font-weight:700; }
.news-source-err { color:#ff5d6c; font-weight:700; }
.news-note { padding:11px 15px; color:#5a6577; font-size:10.5px; line-height:1.5; background:#0a0e14; }
.ai-summary-box { margin-bottom:18px; }
.ai-summary-body { padding:14px 15px; background:#0a0e14; }
.ai-summary-headline { color:#e6ebf2; font-size:15px; line-height:1.45; font-weight:800; margin-bottom:10px; }
.ai-summary-meta { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }
.ai-summary-chip { display:inline-flex; align-items:center; color:#1fd6a8; border:1px solid #1c3a32;
                   background:rgba(31,214,168,.08); border-radius:3px; padding:2px 6px; font-size:10px; font-weight:700; }
.ai-summary-chip.warn { color:#e0b341; border-color:#3a311f; background:rgba(224,179,65,.08); }
.ai-summary-list { margin:0 0 12px 0; padding:0; list-style:none; }
.ai-summary-list li { color:#cdd5e0; line-height:1.48; font-size:11.5px; padding:7px 0; border-bottom:1px solid #11161f; }
.ai-summary-list li:last-child { border-bottom:none; }
.ai-summary-label { color:#e0b341; font-size:10px; letter-spacing:.06em; text-transform:uppercase; margin:13px 0 6px; }
.ai-summary-tags { display:flex; gap:6px; flex-wrap:wrap; }
.ai-summary-tag { color:#8a95a8; border:1px solid #1c2430; border-radius:3px; padding:2px 6px; font-size:10px; }
.ai-summary-error { color:#ff8a93; font-size:10.5px; line-height:1.45; margin-top:10px; }
.ai-summary-box.is-clickable { cursor:pointer; transition:border-color .15s ease, transform .15s ease; }
.ai-summary-box.is-clickable:hover { border-color:#3a4658; transform:translateY(-1px); }
.ai-summary-box.is-clickable:hover .box-head { color:#f0c85e; }
.coin-news-map-panel[hidden] { display:none !important; }
.news-map-sub { color:#5a6577; font-size:10.5px; margin-top:-5px; }
.news-map-stats { grid-template-columns:repeat(4,1fr); }
.news-map-return-row { position:sticky; top:104px; z-index:16; display:flex; align-items:center; gap:10px;
                       margin:-8px 0 12px; padding:8px 0; background:#070b0f; }
.news-map-return-row .mini-btn,
.news-map-back-btn { color:#e6ebf2; border-color:#3a4658; background:#101720; font-weight:800; }
.news-map-return-row .muted { font-size:10.5px; }
.news-map-back-btn { margin-left:2px; }
.news-map-box { margin-bottom:24px; }
.news-map-canvas { position:relative; padding:18px; min-height:548px; background:#070b0f; overflow:hidden; }
.news-map-layout { position:relative; display:grid; grid-template-columns:minmax(0,1fr) minmax(250px,.82fr) minmax(0,1fr);
                   gap:16px; align-items:center; margin-bottom:16px; }
.news-map-layout::before { content:""; position:absolute; left:18%; right:18%; top:50%; height:1px; background:#1c2430; }
.news-map-column { display:grid; gap:12px; }
.news-map-center { display:flex; align-items:center; justify-content:center; min-height:310px; }
.news-map-node { position:relative; z-index:1; border:1px solid #1c2430; border-radius:4px; background:#0d1219; padding:13px 14px; }
.news-map-node.center { width:100%; min-height:230px; display:flex; flex-direction:column; justify-content:center;
                        border-color:#1c3a32; background:#101720; box-shadow:0 0 0 1px rgba(31,214,168,.06) inset; }
.news-map-node h3 { margin:0 0 9px; color:#e0b341; font-size:10.5px; letter-spacing:.08em; text-transform:uppercase; }
.news-map-headline { color:#e6ebf2; font-size:19px; line-height:1.42; font-weight:900; margin-bottom:12px; }
.news-map-meta { display:flex; gap:6px; flex-wrap:wrap; }
.news-map-chip { display:inline-flex; align-items:center; gap:5px; color:#1fd6a8; border:1px solid #1c3a32;
                 background:rgba(31,214,168,.08); border-radius:3px; padding:3px 7px; font-size:10px; font-weight:800; }
.news-map-chip.warn { color:#e0b341; border-color:#3a311f; background:rgba(224,179,65,.08); }
.news-map-list { margin:0; padding:0; list-style:none; display:grid; gap:7px; }
.news-map-list li { color:#cdd5e0; font-size:11.5px; line-height:1.42; padding-bottom:7px; border-bottom:1px solid #11161f; }
.news-map-list li:last-child { padding-bottom:0; border-bottom:none; }
.news-map-tags { display:flex; gap:6px; flex-wrap:wrap; }
.news-map-tag { color:#cdd5e0; border:1px solid #1c2430; background:#0a0e14; border-radius:3px; padding:5px 7px; font-size:10.5px; font-weight:700; }
.news-map-live { display:grid; grid-template-columns:1.04fr .96fr; gap:16px; align-items:start; }
.news-map-news-row { display:grid; grid-template-columns:82px 1fr 72px; gap:10px; align-items:start; padding:9px 0; border-bottom:1px solid #11161f; }
.news-map-news-row:last-child { border-bottom:none; }
.news-map-news-row a { color:#e6ebf2; text-decoration:none; font-size:11.5px; line-height:1.35; font-weight:800; }
.news-map-news-row a:hover { color:#1fd6a8; }
.news-map-source { color:#e0b341; font-size:10px; font-weight:800; }
.news-map-time { color:#5a6577; font-size:10px; text-align:right; white-space:nowrap; }

.tbl-head { padding:9px 15px; font-size:10px; color:#5a6577;
            text-transform:uppercase; letter-spacing:.05em; background:#0d1219;
            border-bottom:1px solid #1c2430; }
.tbl-row { padding:11px 15px; border-bottom:1px solid #11161f; font-size:11.5px; }
.tbl-row:last-child { border-bottom:none; }

.port-grid { display:grid; grid-template-columns:1.1fr .9fr 1fr .7fr; align-items:center; }
.port-head  { display:grid; grid-template-columns:1.1fr .9fr 1fr .7fr; }
.port-bar { height:3px; background:#141a23; border-radius:2px; overflow:hidden; }
.port-bar > span { display:block; height:100%; background:#3a4658; }
.portfolio-detail { display:grid; grid-template-columns:1.25fr .75fr; gap:18px; margin-bottom:24px; }
.portfolio-metrics { display:grid; grid-template-columns:repeat(4,1fr); border-bottom:1px solid #1c2430; }
.portfolio-metric { padding:13px 15px; border-right:1px solid #141a23; background:#0d1219; }
.portfolio-metric:last-child { border-right:none; }
.portfolio-metric .label { font-size:10px; color:#e0b341; letter-spacing:.06em; text-transform:uppercase; margin-bottom:8px; }
.portfolio-metric .val { font-size:17px; font-weight:700; color:#e6ebf2; }
.holding-head, .holding-row { display:grid; grid-template-columns:92px 1fr 102px 86px 86px 78px; align-items:center; }
.allocation-row { display:grid; grid-template-columns:74px 1fr 54px; gap:10px; align-items:center; padding:11px 15px; border-bottom:1px solid #11161f; font-size:11.5px; }
.allocation-row:last-child { border-bottom:none; }
.spark-wrap { height:54px; padding:8px 10px 10px; border-top:1px solid #141a23; }
.manual-panel { border-top:1px solid #141a23; background:#0d1219; }
.manual-form { display:grid; grid-template-columns:1.1fr 1fr 1fr auto auto; gap:7px; padding:11px 15px; align-items:center; }
.manual-form input { min-width:0; font-family:'JetBrains Mono',monospace; background:#0a0e14; color:#e6ebf2;
                     border:1px solid #1c2430; border-radius:3px; padding:6px 8px; font-size:11px; outline:none; }
.manual-form input:focus { border-color:#1fd6a8; }
.manual-rows { border-top:1px solid #141a23; }
.manual-row { display:grid; grid-template-columns:74px 1fr 92px; gap:10px; align-items:center;
              padding:9px 15px; border-bottom:1px solid #11161f; font-size:11px; cursor:pointer; }
.manual-row:last-child { border-bottom:none; }
.manual-row:hover { background:#101720; }
.manual-status { padding:0 15px 10px; color:#5a6577; font-size:10.5px; min-height:20px; }

.hist-grid { display:grid; grid-template-columns:74px 100px 120px 1fr 54px 70px 60px 130px; align-items:center; }
.hist-head { display:grid; grid-template-columns:74px 100px 120px 1fr 54px 70px 60px 130px; }
.action-chip { display:inline-block; min-width:42px; padding:2px 0; border-radius:3px;
               font-weight:700; font-size:10.5px; text-align:center; }
.act-BUY  { color:#1fd6a8; background:rgba(31,214,168,.12); }
.act-SELL { color:#ff5d6c; background:rgba(255,93,108,.12); }
.act-HOLD { color:#e0b341; background:rgba(224,179,65,.12); }

.trade-grid { display:grid; grid-template-columns:130px 100px 60px 1fr 100px 90px 70px 100px 60px; align-items:center; }
.trade-head { display:grid; grid-template-columns:130px 100px 60px 1fr 100px 90px 70px 100px 60px; }
.reason-text { color:#9aa3b5; padding:0 14px; line-height:1.45; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.mini-reason { color:#9aa3b5; line-height:1.45; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }

.err-banner { background:#3a1f23; color:#ff8a93; padding:10px 14px; border-radius:4px;
              margin-bottom:16px; display:none; border:1px solid #5a2f33; font-size:12px; }

/* analyze */
.search-bar { display:flex; gap:10px; align-items:center; border:1px solid #1c2430;
              border-radius:4px; background:#0d1219; padding:14px 16px; margin-bottom:20px; }
.search-bar input { font-family:'JetBrains Mono',monospace; background:#0a0e14; color:#e6ebf2;
                    border:1px solid #1c2430; border-radius:4px; padding:8px 12px;
                    font-size:13px; width:220px; outline:none; }
.search-bar input:focus { border-color:#1fd6a8; }
.iv-group { display:flex; gap:3px; background:#0a0e14; border:1px solid #1c2430;
            border-radius:4px; padding:3px; }
.iv-btn { font-family:'JetBrains Mono',monospace; font-size:11px; padding:6px 11px;
          border-radius:3px; border:none; cursor:pointer; background:transparent; color:#8a95a8; }
.iv-btn.on { background:#1c2430; color:#e6ebf2; font-weight:600; }
.run-btn { font-family:'JetBrains Mono',monospace; background:rgba(31,214,168,.12);
           color:#1fd6a8; border:1px solid #1c3a32; border-radius:4px; padding:8px 18px;
           font-size:13px; font-weight:600; cursor:pointer; }
.run-btn:hover { background:rgba(31,214,168,.2); }
.snap-grid { display:grid; grid-template-columns:repeat(4,1fr); border:1px solid #1c2430;
             border-radius:4px; overflow:hidden; margin-bottom:20px; }
.snap-card { padding:14px 16px; border-right:1px solid #141a23; background:#0d1219; }
.snap-card:last-child { border-right:none; }
.snap-card .label { font-size:10px; color:#e0b341; letter-spacing:.06em;
                    text-transform:uppercase; margin-bottom:9px; }
.snap-card .val { font-size:18px; font-weight:700; }
.snap-card .sub { font-size:11px; color:#5a6577; margin-top:4px; }
.analyze-grid { display:grid; grid-template-columns:1.5fr 1fr; gap:18px; align-items:start; }
.report-row { display:grid; grid-template-columns:84px 1fr; gap:14px; padding:12px 16px;
              border-bottom:1px solid #11161f; }
.report-row:last-child { border-bottom:none; }
.report-row .k { font-size:11px; color:#1fd6a8; letter-spacing:.03em; padding-top:1px; }
.report-row .v { font-size:12.5px; color:#cdd5e0; line-height:1.6;
                 font-family:'IBM Plex Sans', sans-serif; }
.report-note { padding:11px 16px; font-size:10.5px; color:#3a4658; line-height:1.5;
               background:#0a0e14; border-top:1px solid #141a23; }
.context-grid { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:18px; }
.context-row { display:grid; grid-template-columns:96px 1fr; gap:12px; padding:10px 15px; border-bottom:1px solid #11161f; font-size:11.5px; }
.context-row:last-child { border-bottom:none; }
.context-row .k { color:#5a6577; }
.context-row .v { color:#cdd5e0; text-align:right; }
.mini-list-row { display:grid; grid-template-columns:84px 62px 1fr; gap:10px; padding:10px 15px; border-bottom:1px solid #11161f; font-size:11px; align-items:center; }
.mini-list-row:last-child { border-bottom:none; }

.foot { font-size:10.5px; color:#3a4658; margin-top:16px; }

@media (max-width: 980px) {
  .hd-row { flex-wrap:wrap; padding:12px 14px; }
  .tabs { order:4; width:100%; }
  .head-tools { order:5; width:100%; }
  .tab { flex:1; text-align:center; }
  .wrap { padding:16px 12px; }
  .kpi-grid,
  .snap-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .kpi:nth-child(2n),
  .snap-card:nth-child(2n) { border-right:none; }
  .row-grid,
  .market-grid,
  .coin-news-grid,
  .news-map-layout,
  .news-map-live,
  .coin-portfolio-grid,
  .analyze-grid,
  .portfolio-detail,
  .context-grid { grid-template-columns:1fr; }
  .news-map-layout::before { display:none; }
  .news-map-center { min-height:auto; }
  .news-map-canvas { min-height:auto; }
  .box { overflow:auto; }
  .hist-head,
  .hist-grid { min-width:760px; }
  .trade-head,
  .trade-grid { min-width:810px; }
  .holding-head,
  .holding-row { min-width:760px; }
  .coin-holding-head,
  .coin-holding-row { min-width:680px; }
  .coin-market-board-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .coin-market-chip-grid { grid-template-columns:repeat(4,minmax(0,1fr)); }
  .news-item { grid-template-columns:1fr; gap:8px; }
  .news-time { text-align:left; }
  .search-bar { flex-wrap:wrap; align-items:stretch; }
  .section-line { align-items:flex-start; flex-direction:column; }
  .coin-trade-form { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .manual-form { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .manual-form .mini-btn { width:100%; }
}

@media (max-width: 620px) {
  .kpi-grid,
  .market-stat-grid,
  .snap-grid,
  .portfolio-metrics { grid-template-columns:1fr; }
  .kpi,
  .market-stat,
  .snap-card,
  .portfolio-metric { border-right:none; border-bottom:1px solid #141a23; }
  .kpi:last-child,
  .market-stat:last-child,
  .snap-card:last-child,
  .portfolio-metric:last-child { border-bottom:none; }
  .search-bar input,
  .iv-group,
  .run-btn { width:100%; }
  .coin-market-board-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .coin-market-chip-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .coin-board-toolbar .inline-tools { width:100%; }
  .iv-btn { flex:1; }
  .coin-trade-form { grid-template-columns:1fr; }
  .manual-form { grid-template-columns:1fr; }
  .legend { display:none; }
}
"""

FONTS_HEAD = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
"""


def _nav_tabs(active: str) -> str:
    tabs = [
        ("/stocks", "주식", "stocks"),
        ("/coin", "코인", "coin"),
        ("/assets", "내자산", "assets"),
        ("/analyze", "AI 분석", "analyze"),
    ]
    return "\n".join(
        f'<a href="{href}" class="tab{" on" if key == active else ""}">{label}</a>'
        for href, label, key in tabs
    )


def _coin_page_html() -> str:
    try:
        initial_portfolio = _portfolio_payload()
    except Exception as e:  # noqa: BLE001
        initial_portfolio = {"holdings": [], "summary": {}, "account_error": str(e)}
    payload = json.dumps(initial_portfolio, ensure_ascii=False).replace("</", "<\\/")
    script = f"<script>window.__initialCoinPortfolio = {payload};</script>"
    return COIN_HTML.replace("<!-- INITIAL_COIN_PORTFOLIO -->", script)


# 공통 JS 헬퍼: 숫자/PnL/티커테이프/SVG 패스
COMMON_JS = """
const KRW = (n, signed=false) => {
  if (n==null || isNaN(n)) return "—";
  const r = Math.round(n);
  const s = r.toLocaleString("ko-KR");
  return (signed && r>0 ? "+" : "") + s;
};
const PCT = (n, signed=true) => {
  if (n==null || isNaN(n)) return "—";
  return (signed && n>0 ? "+" : "") + n.toFixed(2) + "%";
};
const NUM = (n, digits=6) => {
  if (n==null || isNaN(n)) return "—";
  return Number(n).toLocaleString("ko-KR", {maximumFractionDigits: digits});
};
const colorOf = n => n>0 ? "up" : n<0 ? "down" : "muted";
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}
function cleanReason(text, maxLen=150) {
  if (!text) return "—";
  const s = String(text);
  if (s.includes("RESOURCE_EXHAUSTED") || s.toLowerCase().includes("quota")) {
    return "AI 호출 한도 초과로 판단 생략. API 키/요금제 또는 AI_PROVIDER 설정을 확인하세요.";
  }
  if (s.includes("ClientError") && s.includes("429")) {
    return "AI 호출 한도 초과로 판단 생략. 잠시 후 다시 시도합니다.";
  }
  const compact = s.replace(/\\s+/g, " ").trim();
  return compact.length > maxLen ? compact.slice(0, maxLen - 1) + "…" : compact;
}

// SVG 패스 생성: vals(배열) → svg 좌표 패스
function buildPath(vals, vw, vh, lov, hiv, pad=6) {
  if (!vals || vals.length===0) return "";
  let d = "", started = false;
  for (let i=0; i<vals.length; i++) {
    const v = vals[i];
    if (v==null || isNaN(v)) continue;
    const x = (i / (vals.length - 1)) * vw;
    const y = pad + (vh - 2*pad) * (1 - (v - lov) / (hiv - lov));
    d += (started ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1) + " ";
    started = true;
  }
  return d.trim();
}

function renderChart(svgPriceId, svgRsiId, data) {
  const closes = data.closes, ma5 = data.ma5, ma20 = data.ma20, rsi = data.rsi;
  const lo = Math.min(...closes.filter(v=>v!=null));
  const hi = Math.max(...closes.filter(v=>v!=null));
  const pad = (hi - lo) * 0.14 || 1;
  const L = lo - pad, H = hi + pad;
  const VW = 1000, VHp = 260, VHr = 90;

  const pricePath = buildPath(closes, VW, VHp, L, H);
  const ma5Path   = buildPath(ma5,    VW, VHp, L, H);
  const ma20Path  = buildPath(ma20,   VW, VHp, L, H);
  const areaPath  = pricePath ? pricePath + ` L ${VW} ${VHp} L 0 ${VHp} Z` : "";
  const rsiPath   = buildPath(rsi, VW, VHr, 0, 100, 5);

  document.getElementById(svgPriceId).innerHTML = `
    <path d="${areaPath}" fill="url(#gA-${svgPriceId})" opacity="0.5"></path>
    <path d="${ma20Path}" fill="none" stroke="#5a6577" stroke-width="1.5" vector-effect="non-scaling-stroke"></path>
    <path d="${ma5Path}"  fill="none" stroke="#e0b341" stroke-width="1.5" vector-effect="non-scaling-stroke"></path>
    <path d="${pricePath}" fill="none" stroke="#1fd6a8" stroke-width="2" vector-effect="non-scaling-stroke"></path>
    <defs><linearGradient id="gA-${svgPriceId}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#1fd6a8" stop-opacity="0.3"></stop>
      <stop offset="1" stop-color="#1fd6a8" stop-opacity="0"></stop>
    </linearGradient></defs>`;

  if (svgRsiId) {
    document.getElementById(svgRsiId).innerHTML = `
      <line x1="0" y1="27" x2="${VW}" y2="27" stroke="#2a1a1d" stroke-width="1" vector-effect="non-scaling-stroke"></line>
      <line x1="0" y1="63" x2="${VW}" y2="63" stroke="#1a2a22" stroke-width="1" vector-effect="non-scaling-stroke"></line>
      <path d="${rsiPath}" fill="none" stroke="#5aa3ff" stroke-width="1.6" vector-effect="non-scaling-stroke"></path>`;
  }

  return {
    lastClose: closes[closes.length-1],
    lastRsi: rsi[rsi.length-1],
    lastMa5: ma5[ma5.length-1],
    lastMa20: ma20[ma20.length-1],
    changePct: closes[0] ? (closes[closes.length-1]/closes[0] - 1) * 100 : 0,
  };
}

function applyTerminalHeader(s) {
  const isDry = (s.mode || "").includes("DRY");
  const brand = document.querySelector(".brand");
  if (brand) brand.className = "brand" + (isDry ? " dry" : "");

  const pill = document.getElementById("mode-pill");
  if (pill) {
    pill.className = isDry ? "pill-dry" : "pill-live";
    pill.innerHTML = '<span class="dot"></span><span>' + (isDry ? "DRY · 모의" : "LIVE · 실거래") + '</span>';
  }

  const ai = document.getElementById("ai-pill");
  if (ai) ai.textContent = "AI " + (s.provider || "-") + " · " + (s.model || "-");

  const bot = document.getElementById("bot-pill");
  if (bot) {
    const running = !!s.loop_running;
    const paused = !!s.bot_paused;
    bot.className = "pill-bot " + (paused ? "pause" : running ? "on" : "");
    bot.textContent = paused ? "BOT 일시정지" : running ? "BOT 운용중" : "BOT 대기";
  }

  const pauseBtn = document.getElementById("pause-bot");
  if (pauseBtn) pauseBtn.disabled = !!s.bot_paused;
  const resumeBtn = document.getElementById("resume-bot");
  if (resumeBtn) resumeBtn.disabled = !s.bot_paused;

  const upd = document.getElementById("upd");
  if (upd) upd.textContent = "UPD " + (s.last_update || "—");

  const foot = document.getElementById("foot");
  if (foot) {
    foot.textContent = "시작 " + s.started_at + " · 주기 " + s.interval + "s · " +
      (isDry ? "DRY_RUN=True(모의)" : "DRY_RUN=False(실거래)");
  }
}

function renderTickerTape(items) {
  const tape = document.getElementById("tape");
  if (!tape || !items || items.length === 0) return;
  const cell = it => {
    const col = it.chg_pct >= 0 ? "#1fd6a8" : "#ff5d6c";
    const sign = it.chg_pct >= 0 ? "+" : "";
    return `<span class="tape-cell">
      <span class="sym">${it.sym}</span>
      <span class="px">${KRW(it.price)}</span>
      <span style="color:${col};font-weight:600">${sign}${it.chg_pct.toFixed(2)}%</span>
    </span>`;
  };
  tape.innerHTML = items.map(cell).join("") + items.map(cell).join("");
}

async function loadTickerTape() {
  if (window._tapeLoading || document.hidden) return;
  window._tapeLoading = true;
  try {
    const j = await (await fetch("/api/ticker_quotes")).json();
    renderTickerTape(j.items || []);
  } catch(e) {}
  finally { window._tapeLoading = false; }
}

async function tickTape() {
  await loadTickerTape();
}

async function setBotPaused(paused) {
  const action = paused ? "pause" : "resume";
  if (!paused) {
    const mode = document.getElementById("mode-pill")?.textContent || "";
    if (mode.includes("LIVE") && !confirm("실거래 봇을 재개할까요?")) return;
  }
  try {
    const r = await fetch("/api/control", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ action }),
    });
    const s = await r.json();
    if (!r.ok) throw new Error(s.error || "control failed");
    applyTerminalHeader(s);
  } catch(e) {
    alert(e.message || e);
  }
}
"""

# ===== 코인 전용 페이지 =====
COIN_HTML = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stockagent · 코인</title>
{FONTS_HEAD}
<style>{BASE_CSS}</style>
</head><body>

<div class="hd">
  <div class="hd-row">
    <span class="brand" id="brand">stockagent<span class="cursor">_</span></span>
    <span id="mode-pill" class="pill-live"><span class="dot"></span><span>LIVE · 실거래</span></span>
    <span class="pill-ai" id="ai-pill">AI - · -</span>
    <span class="pill-bot" id="bot-pill">BOT 대기</span>
    <div class="head-tools">
      <button class="mini-btn danger" id="pause-bot" onclick="setBotPaused(true)">일시정지</button>
      <button class="mini-btn" id="resume-bot" onclick="setBotPaused(false)">재개</button>
    </div>
    <span style="flex:1"></span>
    <div class="tabs">
      {_nav_tabs("coin")}
    </div>
    <span class="upd" id="upd">UPD —</span>
  </div>
  <div class="tape-wrap">
    <div class="tape-row" id="tape"></div>
  </div>
</div>

<div class="wrap">
  <div class="err-banner" id="err"></div>

  <div class="section-line coin-section-anchor" id="coin-section-switch">
    <div class="coin-section-title">
      <div class="section-title">코인</div>
      <div class="coin-section-nav">
        <button class="coin-section-btn on" data-coin-section="market" onclick="setCoinSection('market')">마켓</button>
        <button class="coin-section-btn" data-coin-section="portfolio" onclick="setCoinSection('portfolio')">포트폴리오</button>
        <button class="coin-section-btn" data-coin-section="news" onclick="setCoinSection('news')">뉴스</button>
      </div>
    </div>
  </div>

  <div class="coin-panel" id="coin-market-panel">
  <div class="section-line" id="coin-market-section">
    <div class="section-title">코인 마켓</div>
    <div class="market-actions">
      <input class="coin-market-search" id="coin-market-search" autocomplete="off"
             placeholder="코인 검색 BTC/비트코인" oninput="filterCoinTickers(this.value)"
             onkeydown="if(event.key==='Enter') selectFirstCoinMatch()">
      <select class="terminal-select" id="coin-ticker" onchange="setCoinTicker(this.value)"></select>
      <span class="coin-market-count" id="coin-market-count">—</span>
      <button class="mini-btn" data-coin-iv="minute15" onclick="setCoinInterval('minute15')">15분</button>
      <button class="mini-btn on" data-coin-iv="minute60" onclick="setCoinInterval('minute60')">1시간</button>
      <button class="mini-btn" data-coin-iv="day" onclick="setCoinInterval('day')">일봉</button>
    </div>
  </div>

  <div class="market-stat-grid">
    <div class="market-stat"><div class="label">현재가</div><div class="val" id="coin-price">—</div></div>
    <div class="market-stat"><div class="label">구간 변동률</div><div class="val" id="coin-change">—</div></div>
    <div class="market-stat"><div class="label">RSI14</div><div class="val" id="coin-rsi">—</div></div>
    <div class="market-stat"><div class="label">스프레드</div><div class="val" id="coin-spread">—</div></div>
  </div>

  <div class="coin-market-directory">
    <div class="coin-market-directory-head">
      <div class="section-title">전체 코인 목록</div>
      <span class="coin-market-count" id="coin-directory-count">—</span>
    </div>
    <div class="coin-market-chip-grid" id="coin-market-chip-grid"></div>
  </div>

  <div class="section-line coin-board-toolbar">
    <div>
      <div class="section-title">업비트 전체 차트</div>
      <div class="coin-board-sub" id="coin-board-sub">전체 KRW 마켓 미니 차트를 불러오는 중입니다.</div>
    </div>
    <div class="inline-tools">
      <button class="mini-btn" data-coin-page-size="18" onclick="setCoinBoardPageSize(18)">18개</button>
      <button class="mini-btn on" data-coin-page-size="24" onclick="setCoinBoardPageSize(24)">24개</button>
      <button class="mini-btn" onclick="changeCoinBoardPage(-1)">← 이전</button>
      <button class="mini-btn" onclick="changeCoinBoardPage(1)">다음 →</button>
      <button class="mini-btn" onclick="loadCoinMarketBoard(true)">새로고침</button>
    </div>
  </div>
  <div class="coin-market-board-grid" id="coin-market-board-grid"></div>
  <div class="news-note" id="coin-market-board-note">코인 검색 결과를 미니 차트로 보여줍니다. 카드를 누르면 아래 메인 차트와 호가가 바뀝니다.</div>

  <div class="market-grid">
    <div class="box">
      <div class="box-head">
        <span id="coin-chart-title">— · —</span>
        <span style="flex:1"></span>
        <span class="legend"><span class="swatch" style="background:#1fd6a8"></span>종가</span>
        <span class="legend"><span class="swatch" style="background:#e0b341"></span>MA5</span>
        <span class="legend"><span class="swatch" style="background:#5a6577"></span>MA20</span>
      </div>
      <div style="padding:8px 8px 0">
        <svg id="coinSvg" viewBox="0 0 1000 260" preserveAspectRatio="none"
             style="width:100%;height:360px;display:block"></svg>
      </div>
    </div>

    <div class="box">
      <div class="box-head">
        <span>ORDERBOOK</span>
        <span class="total" id="coin-ob-total">—</span>
      </div>
      <div class="tbl-head orderbook-head">
        <span style="text-align:right">매도</span><span style="text-align:right">수량</span>
        <span style="text-align:right">매수</span><span style="text-align:right">수량</span>
      </div>
      <div id="coin-orderbook-rows"></div>
    </div>
  </div>
  </div>

  <div class="coin-panel" id="coin-portfolio-panel" hidden>
  <div class="section-line coin-section-anchor" id="coin-portfolio-section">
    <div class="section-title">내 코인 포트폴리오</div>
    <div class="inline-tools">
      <button class="mini-btn" onclick="loadCoinPortfolio()">새로고침</button>
    </div>
  </div>
  <div class="coin-portfolio-grid">
    <div class="box">
      <div class="box-head">
        <span>MY COINS</span>
        <span class="total" id="coin-pf-total">—</span>
      </div>
      <div class="portfolio-metrics">
        <div class="portfolio-metric"><div class="label">코인 평가</div><div class="val" id="coin-pf-coin-value">—</div></div>
        <div class="portfolio-metric"><div class="label">미실현손익</div><div class="val" id="coin-pf-unrealized">—</div></div>
        <div class="portfolio-metric"><div class="label">총 수익률</div><div class="val" id="coin-pf-return">—</div></div>
        <div class="portfolio-metric"><div class="label">원화</div><div class="val" id="coin-pf-cash">—</div></div>
      </div>
      <div class="tbl-head coin-holding-head">
        <span>자산</span><span style="text-align:right">평가 / 수량</span>
        <span style="text-align:right">평단</span><span style="text-align:right">손익</span><span style="text-align:right">비중</span>
      </div>
      <div id="coin-pf-rows"></div>
    </div>

    <div class="box">
      <div class="box-head">
        <span>ALLOCATION</span>
        <span class="total" id="coin-pf-largest">—</span>
      </div>
      <div class="donut-panel">
        <svg id="coinPfDonut" class="donut-svg" viewBox="0 0 220 220"></svg>
      </div>
      <div id="coin-pf-allocation"></div>
      <div class="coin-pf-note" id="coin-pf-note">업비트 포트폴리오를 불러오는 중입니다.</div>
    </div>
  </div>

  <div class="box coin-trade-box">
    <div class="box-head">
      <span>MANUAL MARKET ORDER</span>
      <span class="coin-trade-mode" id="coin-trade-mode">주문 모드 확인 중</span>
    </div>
    <div class="coin-trade-form">
      <div class="coin-trade-field">
        <label>마켓</label>
        <input class="coin-trade-input" id="coin-trade-ticker" value="KRW-BTC" autocomplete="off">
      </div>
      <div class="coin-trade-field">
        <label>매수 금액 KRW</label>
        <input class="coin-trade-input" id="coin-trade-krw" inputmode="decimal" placeholder="5000">
      </div>
      <div class="coin-trade-field">
        <label>매도 수량</label>
        <input class="coin-trade-input" id="coin-trade-volume" inputmode="decimal" placeholder="0.00000000">
      </div>
      <div class="coin-trade-field">
        <label>실거래 확인</label>
        <input class="coin-trade-input" id="coin-trade-confirm" autocomplete="off" placeholder="LIVE">
      </div>
    </div>
    <div class="coin-trade-actions">
      <button class="mini-btn" onclick="setCoinTradePercent(25)">25%</button>
      <button class="mini-btn" onclick="setCoinTradePercent(50)">50%</button>
      <button class="mini-btn" onclick="setCoinTradePercent(100)">100%</button>
      <button class="mini-btn" onclick="previewManualOrder('buy')">매수 예상</button>
      <button class="mini-btn" onclick="previewManualOrder('sell')">매도 예상</button>
      <button class="mini-btn buy" onclick="executeManualOrder('buy')">시장가 매수</button>
      <button class="mini-btn sell" onclick="executeManualOrder('sell')">시장가 매도</button>
      <span class="coin-trade-mode" id="coin-trade-balance">잔고 —</span>
    </div>
    <div class="coin-trade-status" id="coin-trade-status">모의 모드에서는 실제 업비트 주문을 내지 않습니다.</div>
  </div>
  </div>

  <div class="coin-panel" id="coin-news-panel" hidden>
  <div class="section-line coin-section-anchor" id="coin-news-section">
    <div class="section-title">코인 뉴스</div>
    <div class="inline-tools" id="coin-news-list-tools">
      <select class="terminal-select" id="coin-news-source" onchange="renderCoinNews()">
        <option value="">전체 소스</option>
      </select>
      <select class="terminal-select" id="coin-news-topic" onchange="renderCoinNews()">
        <option value="">전체 토픽</option>
        <option value="btc">BTC</option>
        <option value="eth">ETH</option>
        <option value="xrp">XRP</option>
        <option value="sol">SOL</option>
        <option value="exchange">거래소</option>
        <option value="regulation">규제</option>
        <option value="etf">ETF</option>
      </select>
      <input class="news-search" id="coin-news-query" autocomplete="off" placeholder="검색" oninput="renderCoinNews()">
      <button class="mini-btn" onclick="loadCoinNews(true)">새로고침</button>
    </div>
  </div>
  <div id="coin-news-dashboard">
  <div class="market-stat-grid news-live-grid">
    <div class="market-stat"><div class="label">실시간 뉴스</div><div class="val" id="coin-news-live-count">—</div></div>
    <div class="market-stat"><div class="label">연결 소스</div><div class="val" id="coin-news-live-sources">—</div></div>
    <div class="market-stat"><div class="label">최신 기사</div><div class="val" id="coin-news-latest-time">—</div></div>
    <div class="market-stat"><div class="label">자동 갱신</div><div class="val"><span class="news-live-dot"></span>60초</div></div>
  </div>
  <div class="coin-news-grid">
    <div class="box">
      <div class="box-head">
        <span>LATEST CRYPTO NEWS</span>
        <span class="total" id="coin-news-count">—</span>
      </div>
      <div id="coin-news-rows"></div>
    </div>

    <div>
      <div class="box ai-summary-box is-clickable" role="button" tabindex="0" aria-label="AI 뉴스 맵 보기"
           onclick="openCoinNewsMap()" onkeydown="if(event.key==='Enter'||event.key===' ') openCoinNewsMap()">
        <div class="box-head">
          <span>AI NEWS SUMMARY</span>
          <span class="total" id="coin-news-ai-updated">—</span>
        </div>
        <div class="ai-summary-body" id="coin-news-ai-summary">
          <div class="muted">최신 뉴스를 불러온 뒤 AI 요약을 생성합니다.</div>
        </div>
        <div class="news-note" id="coin-news-ai-note">AI 요약 대기 중입니다.</div>
      </div>

      <div class="box">
        <div class="box-head">
          <span>SOURCES</span>
          <span class="total" id="coin-news-updated">—</span>
        </div>
        <div id="coin-news-sources"></div>
        <div class="news-note" id="coin-news-note">뉴스를 불러오는 중입니다.</div>
      </div>
    </div>
  </div>
  </div>

  <div class="coin-news-map-panel" id="coin-news-map-panel" hidden>
    <div class="section-line">
      <div>
        <div class="section-title">AI 뉴스 맵</div>
        <div class="news-map-sub" id="coin-news-map-sub">LIVE AI NEWS MAP</div>
      </div>
      <div class="inline-tools">
        <button class="mini-btn news-map-back-btn" onclick="closeCoinNewsMap()">← 뉴스 목록</button>
        <button class="mini-btn" onclick="loadCoinNewsSummary(true)">AI 재요약</button>
        <button class="mini-btn" onclick="loadCoinNews(true)">뉴스 새로고침</button>
      </div>
    </div>
    <div class="market-stat-grid news-map-stats">
      <div class="market-stat"><div class="label">맵 업데이트</div><div class="val" id="coin-news-map-updated">—</div></div>
      <div class="market-stat"><div class="label">요약 엔진</div><div class="val" id="coin-news-map-provider">—</div></div>
      <div class="market-stat"><div class="label">분석 기사</div><div class="val" id="coin-news-map-count">—</div></div>
      <div class="market-stat"><div class="label">자동 갱신</div><div class="val"><span class="news-live-dot"></span>60초</div></div>
    </div>
    <div class="news-map-return-row">
      <button class="mini-btn" onclick="closeCoinNewsMap()">← 뉴스 목록으로 돌아가기</button>
      <span class="muted">AI 뉴스 맵에서 일반 뉴스 리스트로 전환합니다.</span>
    </div>
    <div class="box news-map-box">
      <div class="box-head">
        <span>MARKET BRAIN MAP</span>
        <button class="mini-btn news-map-back-btn" onclick="closeCoinNewsMap()">← 뉴스 목록</button>
        <span class="total" id="coin-news-map-sources">—</span>
      </div>
      <div class="news-map-canvas" id="coin-news-map-canvas">
        <div class="muted">AI 뉴스 맵을 준비 중입니다.</div>
      </div>
    </div>
  </div>
  </div>

  <div class="foot" id="foot">—</div>
</div>

<!-- INITIAL_COIN_PORTFOLIO -->
<script>
{COMMON_JS}

const coinIntervalLabel = {{ "minute15":"15분", "minute60":"1시간", "day":"일봉" }};
window._coinTicker = "KRW-BTC";
window._coinInterval = "minute60";
window._coinMarkets = [];
window._coinFilteredMarkets = [];
window._coinChartData = null;
window._coinChartLoading = false;
window._coinQuoteLoading = false;
window._coinOrderbookLoading = false;
window._coinStateLoading = false;
window._coinPortfolioLoading = false;
window._coinBoardPage = 0;
window._coinBoardPageSize = 24;
window._coinBoardTimer = null;
window._coinBoardRequestId = 0;
window._coinBoardClientCache = new Map();
window._coinBoardControllers = [];
window._coinNewsItems = [];
window._coinNewsSources = [];
window._coinNewsErrors = [];
window._coinNewsTotalCount = 0;
window._coinNewsLoaded = false;
window._coinNewsSummaryLoaded = false;
window._coinNewsSummaryPayload = null;
window._coinNewsMapOpen = false;
window._coinNewsSummaryTimer = null;
const coinNewsTopicKeywords = {{
  btc: ["btc", "bitcoin", "비트코인"],
  eth: ["eth", "ethereum", "이더리움"],
  xrp: ["xrp", "ripple", "리플"],
  sol: ["sol", "solana", "솔라나"],
  exchange: ["exchange", "upbit", "bithumb", "coinbase", "binance", "거래소", "업비트", "빗썸", "코인베이스", "바이낸스"],
  regulation: ["regulation", "sec", "cftc", "mifid", "mica", "규제", "소송", "당국"],
  etf: ["etf", "상장지수펀드"],
}};

function normalizeCoinMarket(m) {{
  if (typeof m === "string") {{
    const market = m.toUpperCase();
    const symbol = market.replace("KRW-", "");
    return {{ market, symbol, korean_name:symbol, english_name:symbol }};
  }}
  const market = String(m.market || m.ticker || "").toUpperCase();
  const symbol = String(m.symbol || market.replace("KRW-", ""));
  return {{
    market,
    symbol,
    korean_name: m.korean_name || symbol,
    english_name: m.english_name || symbol,
  }};
}}

function coinMarketLabel(m) {{
  const name = m.korean_name && m.korean_name !== m.symbol ? ` · ${{m.korean_name}}` : "";
  return `${{m.market}}${{name}}`;
}}

function coinMarketMatches(m, query) {{
  if (!query) return true;
  const q = query.trim().toLowerCase();
  return [m.market, m.symbol, m.korean_name, m.english_name]
    .join(" ").toLowerCase().includes(q);
}}

function renderCoinMarketOptions(markets) {{
  const sel = document.getElementById("coin-ticker");
  if (!sel) return;
  const current = window._coinTicker || "KRW-BTC";
  let rows = markets && markets.length ? [...markets] : [];
  const currentMarket = window._coinMarkets.find(m => m.market === current);
  if (currentMarket && !rows.some(m => m.market === current)) rows = [currentMarket, ...rows];
  sel.innerHTML = rows.map(m => `<option value="${{escapeHtml(m.market)}}">${{escapeHtml(coinMarketLabel(m))}}</option>`).join("");
  if (rows.some(m => m.market === current)) sel.value = current;
  const count = document.getElementById("coin-market-count");
  if (count) {{
    const total = window._coinMarkets.length;
    count.textContent = `${{markets.length}}/${{total}} coins`;
  }}
}}

function renderCoinMarketDirectory(markets) {{
  const grid = document.getElementById("coin-market-chip-grid");
  const count = document.getElementById("coin-directory-count");
  if (!grid) return;
  const total = window._coinMarkets.length;
  const rows = markets || [];
  if (count) count.textContent = `${{rows.length}}/${{total}}`;
  grid.innerHTML = rows.map(m => `
    <button class="coin-market-chip${{m.market === window._coinTicker ? " on" : ""}}"
            data-ticker="${{escapeHtml(m.market)}}"
            onclick="setCoinTicker('${{escapeHtml(m.market)}}')">
      <span class="sym">${{escapeHtml(m.symbol)}}</span>
      <span class="name">${{escapeHtml(m.korean_name || m.english_name || m.market)}}</span>
    </button>
  `).join("") || `<span class="muted">검색 결과 없음</span>`;
}}

function filterCoinTickers(query) {{
  const markets = window._coinMarkets || [];
  window._coinFilteredMarkets = markets.filter(m => coinMarketMatches(m, query));
  window._coinBoardPage = 0;
  renderCoinMarketOptions(window._coinFilteredMarkets);
  renderCoinMarketDirectory(window._coinFilteredMarkets);
  scheduleCoinMarketBoard();
}}

function selectFirstCoinMatch() {{
  const first = (window._coinFilteredMarkets || [])[0];
  if (first) setCoinTicker(first.market);
}}

function coinMiniPath(values, w=180, h=58, pad=4) {{
  const vals = (values || []).filter(v => v != null && !isNaN(v));
  if (vals.length < 2) return "";
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  return vals.map((v, i) => {{
    const x = vals.length === 1 ? 0 : (i / (vals.length - 1)) * w;
    const y = pad + (h - pad * 2) * (1 - (v - lo) / span);
    return `${{i ? "L" : "M"}}${{x.toFixed(1)}} ${{y.toFixed(1)}}`;
  }}).join(" ");
}}

function coinBoardPageItems() {{
  const markets = window._coinFilteredMarkets || [];
  const size = window._coinBoardPageSize || 24;
  const pages = Math.max(1, Math.ceil(markets.length / size));
  window._coinBoardPage = Math.max(0, Math.min(window._coinBoardPage || 0, pages - 1));
  const start = window._coinBoardPage * size;
  return markets.slice(start, start + size);
}}

function updateCoinBoardMeta() {{
  const markets = window._coinFilteredMarkets || [];
  const size = window._coinBoardPageSize || 24;
  const pages = Math.max(1, Math.ceil(markets.length / size));
  const sub = document.getElementById("coin-board-sub");
  if (sub) sub.textContent = `전체 ${{window._coinMarkets.length}}개 · 검색 결과 ${{markets.length}}개 · ${{window._coinBoardPage + 1}}/${{pages}} 페이지`;
  const note = document.getElementById("coin-market-board-note");
  if (note) note.textContent = `미니 차트는 현재 검색 결과를 ${{size}}개씩 보여줍니다. 카드를 누르면 아래 메인 차트와 호가가 해당 코인으로 바뀝니다.`;
}}

function updateCoinBoardSelection() {{
  document.querySelectorAll(".coin-mini-card").forEach(card => {{
    card.classList.toggle("on", card.dataset.ticker === window._coinTicker);
  }});
  document.querySelectorAll(".coin-market-chip").forEach(chip => {{
    chip.classList.toggle("on", chip.dataset.ticker === window._coinTicker);
  }});
}}

function coinBoardLoadingCard(m, note="loading") {{
  return `
    <button class="coin-mini-card${{m.market === window._coinTicker ? " on" : ""}}" data-ticker="${{escapeHtml(m.market)}}" onclick="setCoinTicker('${{escapeHtml(m.market)}}')">
      <div class="coin-mini-head">
        <div><div class="coin-mini-symbol">${{escapeHtml(m.symbol)}}</div><div class="coin-mini-name">${{escapeHtml(m.korean_name || m.english_name || m.market)}}</div></div>
        <div class="coin-mini-change muted">—</div>
      </div>
      <div class="coin-mini-price">—</div>
      <svg class="coin-mini-chart" viewBox="0 0 180 58" preserveAspectRatio="none"></svg>
      <div class="coin-mini-note">${{escapeHtml(note)}}</div>
    </button>
  `;
}}

function renderCoinBoardLoading(pageItems) {{
  const grid = document.getElementById("coin-market-board-grid");
  if (!grid) return;
  grid.innerHTML = pageItems.map(m => coinBoardLoadingCard(m)).join("") || `<div class="muted">표시할 코인이 없습니다.</div>`;
}}

function renderCoinBoardItems(items, pageItems, partial=false) {{
  const grid = document.getElementById("coin-market-board-grid");
  if (!grid) return;
  const byTicker = Object.fromEntries((items || []).map(item => [item.ticker, item]));
  grid.innerHTML = pageItems.map(meta => {{
    const item = byTicker[meta.market];
    if (!item && partial) return coinBoardLoadingCard(meta, "loading");
    const finalItem = item || meta;
    const closes = finalItem.closes || [];
    const path = coinMiniPath(closes);
    const chg = Number(finalItem.change_pct);
    const chgText = finalItem.change_pct == null || isNaN(chg) ? "—" : PCT(chg);
    const tone = finalItem.change_pct == null || isNaN(chg) ? "muted" : colorOf(chg);
    const stroke = chg >= 0 ? "#1fd6a8" : "#ff5d6c";
    const ok = finalItem.ok !== false && path;
    return `
      <button class="coin-mini-card${{meta.market === window._coinTicker ? " on" : ""}}" data-ticker="${{escapeHtml(meta.market)}}" onclick="setCoinTicker('${{escapeHtml(meta.market)}}')">
        <div class="coin-mini-head">
          <div>
            <div class="coin-mini-symbol">${{escapeHtml(meta.symbol)}}</div>
            <div class="coin-mini-name">${{escapeHtml(meta.korean_name || meta.english_name || meta.market)}}</div>
          </div>
          <div class="coin-mini-change ${{tone}}">${{escapeHtml(chgText)}}</div>
        </div>
        <div class="coin-mini-price">${{finalItem.price == null || isNaN(Number(finalItem.price)) ? "—" : KRW(Number(finalItem.price)) + " 원"}}</div>
        <svg class="coin-mini-chart" viewBox="0 0 180 58" preserveAspectRatio="none">
          ${{ok ? `<path d="${{path}}" fill="none" stroke="${{stroke}}" stroke-width="2" vector-effect="non-scaling-stroke"></path>` : `<text x="90" y="32" text-anchor="middle" fill="#5a6577" font-size="10">NO CHART</text>`}}
        </svg>
        <div class="coin-mini-note">${{escapeHtml(meta.market)}} · ${{coinIntervalLabel[window._coinInterval] || window._coinInterval}}</div>
      </button>
    `;
  }}).join("") || `<div class="muted">표시할 코인이 없습니다.</div>`;
}}

function scheduleCoinMarketBoard(force=false) {{
  clearTimeout(window._coinBoardTimer);
  window._coinBoardTimer = setTimeout(() => loadCoinMarketBoard(force), force ? 0 : 250);
}}

function changeCoinBoardPage(delta) {{
  const markets = window._coinFilteredMarkets || [];
  const size = window._coinBoardPageSize || 24;
  const pages = Math.max(1, Math.ceil(markets.length / size));
  window._coinBoardPage = Math.max(0, Math.min((window._coinBoardPage || 0) + delta, pages - 1));
  loadCoinMarketBoard();
}}

function setCoinBoardPageSize(size) {{
  window._coinBoardPageSize = Math.max(12, Math.min(24, Number(size) || 24));
  window._coinBoardPage = 0;
  document.querySelectorAll("[data-coin-page-size]").forEach(b => {{
    b.classList.toggle("on", Number(b.dataset.coinPageSize) === window._coinBoardPageSize);
  }});
  loadCoinMarketBoard(true);
}}

async function fetchJsonWithTimeout(url, timeoutMs=25000, controller=null) {{
  const localController = controller || new AbortController();
  const timer = setTimeout(() => localController.abort(), timeoutMs);
  try {{
    const response = await fetch(url, {{ signal: localController.signal }});
    if (!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
    return await response.json();
  }} finally {{
    clearTimeout(timer);
  }}
}}

function coinBoardCacheKey(pageItems) {{
  const tickers = pageItems.map(m => m.market).join(",");
  return `${{tickers}}|${{window._coinInterval || "minute60"}}|36`;
}}

function abortCoinBoardRequests() {{
  (window._coinBoardControllers || []).forEach(controller => {{
    try {{ controller.abort(); }} catch(e) {{}}
  }});
  window._coinBoardControllers = [];
}}

async function loadCoinMarketBoard(force=false) {{
  if (document.hidden) return;
  const pageItems = coinBoardPageItems();
  updateCoinBoardMeta();
  if (!pageItems.length) return;
  const cacheKey = coinBoardCacheKey(pageItems);
  const cached = window._coinBoardClientCache.get(cacheKey);
  if (!force && cached && Date.now() - cached.ts < 180000) {{
    renderCoinBoardItems(cached.items, pageItems, false);
    updateCoinBoardSelection();
    return;
  }}
  abortCoinBoardRequests();
  renderCoinBoardLoading(pageItems);
  const requestId = ++window._coinBoardRequestId;
  const note = document.getElementById("coin-market-board-note");
  if (note) note.style.color = "";
  const collected = [];
  try {{
    const chunks = [];
    for (let i = 0; i < pageItems.length; i += 6) chunks.push(pageItems.slice(i, i + 6));
    const jobs = chunks.map(async chunk => {{
      const controller = new AbortController();
      window._coinBoardControllers.push(controller);
      const tickers = chunk.map(m => m.market).join(",");
      const url = `/api/coin/mini_charts?tickers=${{encodeURIComponent(tickers)}}&interval=${{window._coinInterval || "minute60"}}&count=36${{force ? "&refresh=1" : ""}}`;
      const j = await fetchJsonWithTimeout(url, 18000, controller);
      if (requestId !== window._coinBoardRequestId) return;
      collected.push(...(j.items || []));
      renderCoinBoardItems(collected, pageItems, true);
      updateCoinBoardSelection();
    }});
    const settled = await Promise.allSettled(jobs);
    if (requestId !== window._coinBoardRequestId) return;
    const failed = settled.find(result => result.status === "rejected");
    if (failed) throw failed.reason;
    window._coinBoardClientCache.set(cacheKey, {{ ts: Date.now(), items: collected.slice() }});
    renderCoinBoardItems(collected, pageItems, false);
    updateCoinBoardSelection();
  }} catch(e) {{
    if (e.name === "AbortError") return;
    renderCoinBoardItems(collected, pageItems, false);
    if (note) {{
      note.textContent = e.message || String(e);
      note.style.color = "#ff8a93";
    }}
  }} finally {{
    if (requestId === window._coinBoardRequestId) window._coinBoardControllers = [];
  }}
}}

async function loadCoinConfig() {{
  try {{
    const cfg = await (await fetch("/api/config")).json();
    window._coinConfig = cfg;
    const marketsRaw = cfg.coin_markets && cfg.coin_markets.length
      ? cfg.coin_markets
      : (cfg.tickers && cfg.tickers.length ? cfg.tickers : ["KRW-BTC"]);
    window._coinMarkets = marketsRaw.map(normalizeCoinMarket).filter(m => m.market.startsWith("KRW-"));
    const preferred = window._coinMarkets.some(m => m.market === "KRW-BTC") ? "KRW-BTC" : window._coinMarkets[0]?.market || "KRW-BTC";
    window._coinTicker = preferred;
    const search = document.getElementById("coin-market-search");
    if (search) search.value = "";
    filterCoinTickers("");
    const sel = document.getElementById("coin-ticker");
    if (sel) sel.value = window._coinTicker;
    tickCoinChart();
    tickCoinOrderbook();
    tickCoinQuote();
    updateCoinTradeMode();
  }} catch(e) {{}}
}}

function setCoinTicker(ticker) {{
  window._coinTicker = (ticker || "KRW-BTC").toUpperCase();
  const search = document.getElementById("coin-market-search");
  renderCoinMarketOptions(window._coinFilteredMarkets && window._coinFilteredMarkets.length ? window._coinFilteredMarkets : window._coinMarkets);
  const sel = document.getElementById("coin-ticker");
  if (sel) sel.value = window._coinTicker;
  updateCoinBoardSelection();
  tickCoinChart();
  tickCoinOrderbook();
  tickCoinQuote();
}}

function setCoinInterval(interval) {{
  window._coinInterval = interval;
  document.querySelectorAll("[data-coin-iv]").forEach(b => b.classList.toggle("on", b.dataset.coinIv === interval));
  tickCoinChart();
  loadCoinMarketBoard(true);
}}

function setCoinSection(section) {{
  section = section === "portfolio" || section === "news" ? section : "market";
  window._coinSection = section;
  document.querySelectorAll("[data-coin-section]").forEach(b => {{
    b.classList.toggle("on", b.dataset.coinSection === section);
  }});
  const marketPanel = document.getElementById("coin-market-panel");
  const portfolioPanel = document.getElementById("coin-portfolio-panel");
  const newsPanel = document.getElementById("coin-news-panel");
  if (marketPanel) marketPanel.hidden = section !== "market";
  if (portfolioPanel) portfolioPanel.hidden = section !== "portfolio";
  if (newsPanel) newsPanel.hidden = section !== "news";
  if (section === "portfolio") {{
    loadCoinPortfolio();
  }} else if (section === "news") {{
    if (window._coinNewsLoaded) {{
      renderCoinNews();
      if (!window._coinNewsSummaryLoaded) loadCoinNewsSummary();
    }} else {{
      loadCoinNews();
    }}
  }} else {{
    tickCoinChart();
    tickCoinOrderbook();
    tickCoinQuote();
    if (!(window._coinFilteredMarkets || []).length && (window._coinMarkets || []).length) filterCoinTickers(document.getElementById("coin-market-search")?.value || "");
    else scheduleCoinMarketBoard();
  }}
  const switcher = document.getElementById("coin-section-switch");
  if (switcher) switcher.scrollIntoView({{ behavior: "smooth", block: "start" }});
}}

async function tickCoinChart() {{
  if (document.hidden || (window._coinSection && window._coinSection !== "market")) return;
  if (window._coinChartLoading) return;
  window._coinChartLoading = true;
  try {{
    const ticker = window._coinTicker || "KRW-BTC";
    const interval = window._coinInterval || "minute60";
    const j = await (await fetch(`/api/candles?ticker=${{ticker}}&interval=${{interval}}&count=140`)).json();
    if (j.error) throw new Error(j.error);
    window._coinChartData = j;
    const stats = renderChart("coinSvg", null, j);
    document.getElementById("coin-chart-title").textContent = ticker + " · " + coinIntervalLabel[interval];
    document.getElementById("coin-price").textContent = KRW(stats.lastClose) + " 원";
    document.getElementById("coin-change").textContent = PCT(stats.changePct);
    document.getElementById("coin-change").className = "val " + colorOf(stats.changePct);
    document.getElementById("coin-rsi").textContent = stats.lastRsi != null ? stats.lastRsi.toFixed(1) : "—";
  }} catch(e) {{
    const err = document.getElementById("err");
    err.style.display = "block";
    err.textContent = e.message || e;
  }} finally {{
    window._coinChartLoading = false;
  }}
}}

function applyCoinLivePrice(price) {{
  if (price == null || isNaN(price)) return;
  const data = window._coinChartData;
  if (data && data.closes && data.closes.length) {{
    const live = {{
      ...data,
      closes: data.closes.slice(),
      ma5: (data.ma5 || []).slice(),
      ma20: (data.ma20 || []).slice(),
      rsi: (data.rsi || []).slice(),
    }};
    live.closes[live.closes.length - 1] = Number(price);
    const stats = renderChart("coinSvg", null, live);
    const change = live.closes[0] ? (Number(price) / live.closes[0] - 1) * 100 : stats.changePct;
    document.getElementById("coin-price").textContent = KRW(price) + " 원";
    document.getElementById("coin-change").textContent = PCT(change);
    document.getElementById("coin-change").className = "val " + colorOf(change);
    document.getElementById("coin-rsi").textContent = stats.lastRsi != null ? stats.lastRsi.toFixed(1) : "—";
  }} else {{
    document.getElementById("coin-price").textContent = KRW(price) + " 원";
  }}
}}

async function tickCoinQuote() {{
  if (document.hidden || (window._coinSection && window._coinSection !== "market") || window._coinQuoteLoading) return;
  window._coinQuoteLoading = true;
  try {{
    const ticker = window._coinTicker || "KRW-BTC";
    const j = await (await fetch(`/api/coin/quote?ticker=${{ticker}}`)).json();
    if (j.error) throw new Error(j.error);
    applyCoinLivePrice(Number(j.price));
  }} catch(e) {{}}
  finally {{ window._coinQuoteLoading = false; }}
}}

async function tickCoinOrderbook() {{
  if (document.hidden || (window._coinSection && window._coinSection !== "market") || window._coinOrderbookLoading) return;
  window._coinOrderbookLoading = true;
  try {{
    const ticker = window._coinTicker || "KRW-BTC";
    const j = await (await fetch(`/api/coin/orderbook?ticker=${{ticker}}`)).json();
    if (j.error) throw new Error(j.error);
    const units = (j.units || []).slice(0, 12);
    const best = units[0] || {{}};
    const spread = best.ask_price && best.bid_price ? best.ask_price - best.bid_price : null;
    document.getElementById("coin-spread").textContent = spread == null ? "—" : KRW(spread) + " 원";
    document.getElementById("coin-ob-total").textContent =
      `ASK ${{NUM(j.total_ask_size)}} · BID ${{NUM(j.total_bid_size)}}`;
    document.getElementById("coin-orderbook-rows").innerHTML = units.map(u => `
      <div class="orderbook-row">
        <span class="down" style="text-align:right;font-weight:700">${{KRW(u.ask_price)}}</span>
        <span style="text-align:right;color:#8a95a8">${{NUM(u.ask_size)}}</span>
        <span class="up" style="text-align:right;font-weight:700">${{KRW(u.bid_price)}}</span>
        <span style="text-align:right;color:#8a95a8">${{NUM(u.bid_size)}}</span>
      </div>
    `).join("") || `<div class="tbl-row muted">호가 데이터 없음</div>`;
  }} catch(e) {{}}
  finally {{ window._coinOrderbookLoading = false; }}
}}

function renderCoinPortfolioDonut(holdings, totalValue) {{
  const svg = document.getElementById("coinPfDonut");
  if (!svg) return;
  const colors = ["#1fd6a8", "#e0b341", "#5aa3ff", "#ff5d6c", "#9aa3b5", "#7cdbff"];
  const items = (holdings || []).filter(h => Number(h.current_value || 0) > 0);
  if (!items.length || totalValue <= 0) {{
    svg.innerHTML = `<circle cx="110" cy="110" r="78" fill="none" stroke="#141a23" stroke-width="24"></circle>
      <text x="110" y="104" text-anchor="middle" fill="#5a6577" font-size="12" font-weight="700">NO COINS</text>
      <text x="110" y="124" text-anchor="middle" fill="#3a4658" font-size="10">PORTFOLIO</text>`;
    return;
  }}
  const r = 78;
  const c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = items.map((h, idx) => {{
    const value = Number(h.current_value || 0);
    const dash = Math.max(0, value / totalValue * c);
    const arc = `<circle cx="110" cy="110" r="${{r}}" fill="none" stroke="${{colors[idx % colors.length]}}"
      stroke-width="24" stroke-linecap="round" stroke-dasharray="${{dash.toFixed(2)}} ${{(c - dash).toFixed(2)}}"
      stroke-dashoffset="${{offset.toFixed(2)}}" transform="rotate(-90 110 110)"></circle>`;
    offset -= dash;
    return arc;
  }}).join("");
  const top = items[0];
  svg.innerHTML = `<circle cx="110" cy="110" r="${{r}}" fill="none" stroke="#141a23" stroke-width="24"></circle>
    ${{arcs}}
    <text x="110" y="100" text-anchor="middle" fill="#e6ebf2" font-size="18" font-weight="800">${{KRW(totalValue)}}</text>
    <text x="110" y="120" text-anchor="middle" fill="#8a95a8" font-size="10">COIN VALUE</text>
    <text x="110" y="138" text-anchor="middle" fill="#e0b341" font-size="11" font-weight="700">${{top.currency}} ${{Number(top._coinWeight || 0).toFixed(1)}}%</text>`;
}}

function renderCoinPortfolio(pf) {{
  const s = pf.summary || {{}};
  const holdings = ((pf.holdings || []).filter(h => h.currency !== "KRW"))
    .sort((a, b) => Number(b.current_value || 0) - Number(a.current_value || 0));
  const coinValue = holdings.reduce((sum, h) => sum + Number(h.current_value || 0), 0);
  const coinPrincipal = holdings.reduce((sum, h) => sum + Number(h.principal || 0), 0);
  const coinUnrealized = holdings.reduce((sum, h) => sum + Number(h.unrealized_pnl || 0), 0);
  const coinReturn = coinPrincipal > 0 ? (coinValue / coinPrincipal - 1) * 100 : 0;
  holdings.forEach(h => h._coinWeight = coinValue > 0 ? Number(h.current_value || 0) / coinValue * 100 : 0);

  document.getElementById("coin-pf-total").textContent = KRW(s.total_value || coinValue) + " 원";
  document.getElementById("coin-pf-coin-value").textContent = KRW(coinValue) + " 원";
  document.getElementById("coin-pf-unrealized").textContent = KRW(coinUnrealized, true) + " 원";
  document.getElementById("coin-pf-unrealized").className = "val " + colorOf(coinUnrealized);
  document.getElementById("coin-pf-return").textContent = PCT(coinReturn);
  document.getElementById("coin-pf-return").className = "val " + colorOf(coinReturn);
  document.getElementById("coin-pf-cash").textContent = KRW(s.cash_value || 0) + " 원";

  document.getElementById("coin-pf-rows").innerHTML = holdings.map(h => {{
    const pnl = Number(h.unrealized_pnl || 0);
    const ret = Number(h.return_pct || 0);
    return `
      <div class="tbl-row coin-holding-row">
        <div>
          <div style="font-weight:800;font-size:13px">${{escapeHtml(h.currency || "—")}}</div>
          <div class="muted" style="font-size:10px;margin-top:2px">${{escapeHtml(h.ticker || "")}}</div>
        </div>
        <div style="text-align:right">
          <div>${{KRW(h.current_value)}} 원</div>
          <div class="muted" style="font-size:10px;margin-top:2px">${{NUM(h.balance)}} 개</div>
        </div>
        <div style="text-align:right;color:#9aa3b5">${{KRW(h.avg_buy_price)}} 원</div>
        <div style="text-align:right">
          <div class="${{colorOf(pnl)}}" style="font-weight:700">${{KRW(pnl, true)}}</div>
          <div class="${{colorOf(ret)}}" style="font-size:10px;margin-top:2px">${{PCT(ret)}}</div>
        </div>
        <div style="text-align:right">
          <div style="font-size:11px;color:#8a95a8;margin-bottom:5px">${{Number(h._coinWeight || 0).toFixed(1)}}%</div>
          <div class="port-bar"><span style="width:${{Math.min(100, Number(h._coinWeight || 0))}}%"></span></div>
        </div>
      </div>`;
  }}).join("") || `<div class="tbl-row muted">보유 코인 없음</div>`;

  const colors = ["#1fd6a8", "#e0b341", "#5aa3ff", "#ff5d6c", "#9aa3b5", "#7cdbff"];
  document.getElementById("coin-pf-allocation").innerHTML = holdings.map((h, idx) => `
    <div class="allocation-row">
      <span style="font-weight:700;color:${{colors[idx % colors.length]}}">${{escapeHtml(h.currency || "—")}}</span>
      <div class="port-bar"><span style="width:${{Math.min(100, Number(h._coinWeight || 0))}}%;background:${{colors[idx % colors.length]}}"></span></div>
      <span style="text-align:right;color:#8a95a8">${{Number(h._coinWeight || 0).toFixed(1)}}%</span>
    </div>
  `).join("") || `<div class="tbl-row muted">배분 데이터 없음</div>`;

  const largest = holdings[0];
  document.getElementById("coin-pf-largest").textContent = largest ? `${{largest.currency}} ${{Number(largest._coinWeight || 0).toFixed(1)}}%` : "—";
  const note = document.getElementById("coin-pf-note");
  if (pf.account_error) {{
    note.textContent = "업비트 계좌 조회 실패 · " + pf.account_error;
    note.style.color = "#ff8a93";
  }} else {{
    note.textContent = `코인 원금 ${{KRW(coinPrincipal)}}원 · 평가 ${{KRW(coinValue)}}원 · 현금 ${{KRW(s.cash_value || 0)}}원`;
    note.style.color = "#3a4658";
  }}
  renderCoinPortfolioDonut(holdings, coinValue);
  updateCoinTradeFromPortfolio(pf);
}}

function coinTradeHolding() {{
  const ticker = (document.getElementById("coin-trade-ticker")?.value || "").trim().toUpperCase();
  const holdings = (window._lastCoinPortfolio?.holdings || []).filter(h => h.currency !== "KRW");
  return holdings.find(h => (h.ticker || "").toUpperCase() === ticker) || null;
}}

function updateCoinTradeMode() {{
  const cfg = window._coinConfig || {{}};
  const mode = document.getElementById("coin-trade-mode");
  const confirm = document.getElementById("coin-trade-confirm");
  if (!mode) return;
  const dry = cfg.dry_run !== false;
  mode.textContent = dry ? "DRY RUN · 모의 주문" : "LIVE · 실거래 주문";
  mode.style.color = dry ? "#e0b341" : "#ff5d6c";
  if (confirm) {{
    confirm.disabled = dry;
    confirm.placeholder = dry ? "모의 주문" : "LIVE";
    if (dry) confirm.value = "";
  }}
}}

function updateCoinTradeFromPortfolio(pf) {{
  window._lastCoinPortfolio = pf;
  const holdings = (pf.holdings || []).filter(h => h.currency !== "KRW");
  const input = document.getElementById("coin-trade-ticker");
  if (input && (!input.value || input.value === "KRW-BTC") && holdings[0]) {{
    input.value = holdings[0].ticker || `KRW-${{holdings[0].currency}}`;
  }}
  const balance = document.getElementById("coin-trade-balance");
  const current = coinTradeHolding();
  const cash = Number(pf.summary?.cash_value || 0);
  if (balance) {{
    balance.textContent = current
      ? `원화 ${{KRW(cash)}}원 · 보유 ${{NUM(current.balance)}} ${{current.currency}}`
      : `원화 ${{KRW(cash)}}원 · 선택 종목 미보유`;
  }}
  updateCoinTradeMode();
}}

function setCoinTradeStatus(message, tone="") {{
  const el = document.getElementById("coin-trade-status");
  if (!el) return;
  el.className = "coin-trade-status" + (tone ? " " + tone : "");
  el.textContent = message;
}}

function setCoinTradePercent(percent) {{
  const h = coinTradeHolding();
  if (!h) {{
    setCoinTradeStatus("선택한 종목의 보유 수량이 없습니다.", "warn");
    return;
  }}
  const volume = Number(h.balance || 0) * Number(percent || 0) / 100;
  document.getElementById("coin-trade-volume").value = volume > 0 ? volume.toFixed(8).replace(/0+$/, "").replace(/\\.$/, "") : "";
  setCoinTradeStatus(`${{h.currency}} 보유 수량의 ${{percent}}% 매도 수량을 입력했습니다.`, "ok");
}}

function coinTradePayload(side) {{
  const ticker = (document.getElementById("coin-trade-ticker").value || "").trim().toUpperCase();
  return {{
    side,
    ticker,
    krw_amount: document.getElementById("coin-trade-krw").value,
    volume: document.getElementById("coin-trade-volume").value,
    confirm: document.getElementById("coin-trade-confirm").value,
  }};
}}

function describeManualOrder(ctx) {{
  const side = ctx.side === "buy" ? "매수" : "매도";
  const mode = ctx.dry_run ? "모의" : "실거래";
  return `${{mode}} ${{side}} · ${{ctx.ticker}} · 예상 ${{KRW(ctx.krw_amount)}}원 · 수량 ${{NUM(ctx.volume)}} · 현재가 ${{KRW(ctx.price)}}원 · 수수료 약 ${{KRW(ctx.estimated_fee)}}원`;
}}

async function previewManualOrder(side) {{
  try {{
    const r = await fetch("/api/manual_order/preview", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(coinTradePayload(side)),
    }});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "주문 예상 실패");
    setCoinTradeStatus(describeManualOrder(j.preview), "ok");
  }} catch(e) {{
    setCoinTradeStatus(e.message || String(e), "err");
  }}
}}

async function executeManualOrder(side) {{
  const cfg = window._coinConfig || {{}};
  if (cfg.dry_run === false && (document.getElementById("coin-trade-confirm").value || "").trim().toUpperCase() !== "LIVE") {{
    setCoinTradeStatus("실거래 주문은 확인 입력란에 LIVE를 입력해야 합니다.", "warn");
    return;
  }}
  try {{
    setCoinTradeStatus("주문 처리 중입니다.", "warn");
    const r = await fetch("/api/manual_order", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(coinTradePayload(side)),
    }});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "주문 실패");
    setCoinTradeStatus(describeManualOrder(j.order) + " · 처리 완료", j.order.dry_run ? "warn" : "ok");
    if (j.portfolio) renderCoinPortfolio(j.portfolio);
    else loadCoinPortfolio();
  }} catch(e) {{
    setCoinTradeStatus(e.message || String(e), "err");
  }}
}}

async function loadCoinPortfolio() {{
  if (document.hidden || (window._coinSection && window._coinSection !== "portfolio") || window._coinPortfolioLoading) return;
  window._coinPortfolioLoading = true;
  try {{
    const pf = await (await fetch("/api/portfolio")).json();
    renderCoinPortfolio(pf);
  }} catch(e) {{
    const note = document.getElementById("coin-pf-note");
    if (note) {{
      note.textContent = e.message || String(e);
      note.style.color = "#ff8a93";
    }}
  }} finally {{
    window._coinPortfolioLoading = false;
  }}
}}

function formatCoinNewsTime(item) {{
  const ts = Number(item.published_ts || 0);
  if (!ts) return item.published || "—";
  const d = new Date(ts * 1000);
  if (isNaN(d.getTime())) return item.published || "—";
  return d.toLocaleString("ko-KR", {{ month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" }});
}}

function formatCoinNewsAge(item) {{
  const ts = Number(item.published_ts || 0);
  if (!ts) return "";
  const minutes = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (minutes < 1) return "방금";
  if (minutes < 60) return minutes + "분 전";
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours + "시간 전";
  return Math.round(hours / 24) + "일 전";
}}

function renderCoinNewsSourceFilter() {{
  const sel = document.getElementById("coin-news-source");
  if (!sel) return;
  const current = sel.value || "";
  const names = [...new Set((window._coinNewsSources || []).map(s => s.name).filter(Boolean))];
  sel.innerHTML = `<option value="">전체 소스</option>` +
    names.map(name => `<option value="${{escapeHtml(name)}}">${{escapeHtml(name)}}</option>`).join("");
  if (names.includes(current)) sel.value = current;
}}

function renderCoinNewsSources(payload) {{
  const sources = payload?.sources || window._coinNewsSources || [];
  const errors = payload?.errors || window._coinNewsErrors || [];
  const rows = document.getElementById("coin-news-sources");
  if (rows) {{
    rows.innerHTML = sources.map(s => `
      <div class="news-source-row">
        <span style="font-weight:700;color:#cdd5e0">${{escapeHtml(s.name || "—")}}</span>
        <span style="text-align:right;color:#8a95a8">${{s.count || 0}}건</span>
        <span class="${{s.ok ? "news-source-ok" : "news-source-err"}}">${{s.ok ? "OK" : "ERR"}}</span>
      </div>
    `).join("") || `<div class="tbl-row muted">소스 없음</div>`;
  }}
  const updated = document.getElementById("coin-news-updated");
  if (updated && payload?.fetched_at) {{
    const d = new Date(payload.fetched_at);
    updated.textContent = isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ko-KR", {{ hour:"2-digit", minute:"2-digit" }});
  }}
  const note = document.getElementById("coin-news-note");
  if (note) {{
    const okCount = sources.filter(s => s.ok).length;
    note.textContent = errors.length
      ? `${{okCount}}개 소스 연결 · 일부 소스 오류 ${{errors.length}}건`
      : `${{okCount}}개 소스 연결 · 실제 최신 뉴스 피드`;
    note.style.color = errors.length ? "#e0b341" : "#5a6577";
  }}
  const liveCount = document.getElementById("coin-news-live-count");
  if (liveCount) liveCount.textContent = (window._coinNewsTotalCount || (window._coinNewsItems || []).length) + "건";
  const liveSources = document.getElementById("coin-news-live-sources");
  if (liveSources) liveSources.textContent = `${{sources.filter(s => s.ok).length}}/${{sources.length}}`;
  const latestTime = document.getElementById("coin-news-latest-time");
  if (latestTime) latestTime.textContent = window._coinNewsItems?.[0] ? formatCoinNewsAge(window._coinNewsItems[0]) : "—";
}}

function renderCoinNews() {{
  const source = document.getElementById("coin-news-source")?.value || "";
  const topic = document.getElementById("coin-news-topic")?.value || "";
  const query = (document.getElementById("coin-news-query")?.value || "").trim().toLowerCase();
  let items = window._coinNewsItems || [];
  if (source) items = items.filter(item => item.source === source);
  if (topic && coinNewsTopicKeywords[topic]) {{
    const keys = coinNewsTopicKeywords[topic];
    items = items.filter(item => {{
      const text = [item.title, item.summary, item.publisher, item.source].join(" ").toLowerCase();
      return keys.some(k => text.includes(k.toLowerCase()));
    }});
  }}
  if (query) {{
    items = items.filter(item => [item.title, item.summary, item.publisher, item.source]
      .join(" ").toLowerCase().includes(query));
  }}
  const count = document.getElementById("coin-news-count");
  if (count) count.textContent = items.length + "건";
  const rows = document.getElementById("coin-news-rows");
  if (!rows) return;
  rows.innerHTML = items.map(item => `
    <div class="news-item">
      <div>
        <span class="news-source-chip">${{escapeHtml(item.source || "NEWS")}}</span>
        ${{item.live ? `<span class="news-source-chip live">LIVE</span>` : ""}}
      </div>
      <div>
        <div class="news-publisher">${{escapeHtml(item.publisher || item.source || "")}}</div>
        <a class="news-title" href="${{escapeHtml(item.link || "#")}}" target="_blank" rel="noopener noreferrer">
          ${{escapeHtml(item.title || "제목 없음")}}
        </a>
        <div class="news-summary">${{escapeHtml(item.summary || "요약 없음")}}</div>
      </div>
      <div class="news-time">
        <div>${{escapeHtml(formatCoinNewsAge(item))}}</div>
        <div class="muted" style="margin-top:4px">${{escapeHtml(formatCoinNewsTime(item))}}</div>
      </div>
    </div>
  `).join("") || `<div class="tbl-row muted">표시할 뉴스가 없습니다</div>`;
}}

function aiList(title, items) {{
  const rows = (items || []).filter(Boolean).map(x => `<li>${{escapeHtml(x)}}</li>`).join("");
  if (!rows) return "";
  return `<div class="ai-summary-label">${{escapeHtml(title)}}</div><ul class="ai-summary-list">${{rows}}</ul>`;
}}

function coinNewsMapList(title, items, emptyText="데이터 없음") {{
  const rows = (items || []).filter(Boolean).slice(0, 5)
    .map(x => `<li>${{escapeHtml(String(x))}}</li>`).join("");
  return `
    <section class="news-map-node">
      <h3>${{escapeHtml(title)}}</h3>
      <ul class="news-map-list">${{rows || `<li class="muted">${{escapeHtml(emptyText)}}</li>`}}</ul>
    </section>
  `;
}}

function coinNewsMapTags(title, tags, emptyText="태그 없음") {{
  const rows = (tags || []).filter(Boolean).slice(0, 10)
    .map(x => `<span class="news-map-tag">${{escapeHtml(String(x))}}</span>`).join("");
  return `
    <section class="news-map-node">
      <h3>${{escapeHtml(title)}}</h3>
      <div class="news-map-tags">${{rows || `<span class="news-map-tag muted">${{escapeHtml(emptyText)}}</span>`}}</div>
    </section>
  `;
}}

function coinNewsMapClusters(items) {{
  const defs = [
    ["BTC", ["btc", "bitcoin", "비트코인"]],
    ["ETH", ["eth", "ethereum", "이더리움"]],
    ["XRP", ["xrp", "ripple", "리플"]],
    ["SOL", ["sol", "solana", "솔라나"]],
    ["ETF", ["etf", "상장지수펀드"]],
    ["규제", ["regulation", "sec", "cftc", "규제", "소송", "당국"]],
    ["거래소", ["exchange", "upbit", "bithumb", "coinbase", "binance", "거래소", "업비트", "빗썸"]],
  ];
  return defs.map(([label, keys]) => {{
    const count = (items || []).filter(item => {{
      const text = [item.title, item.summary, item.publisher, item.source].join(" ").toLowerCase();
      return keys.some(k => text.includes(k.toLowerCase()));
    }}).length;
    return count > 0 ? `${{label}} · ${{count}}건` : null;
  }}).filter(Boolean);
}}

function renderCoinNewsMap(payload=window._coinNewsSummaryPayload) {{
  const canvas = document.getElementById("coin-news-map-canvas");
  if (!canvas) return;
  const summary = payload?.summary || {{}};
  const items = window._coinNewsItems || [];
  const sources = window._coinNewsSources || payload?.sources || [];
  const okSources = sources.filter(src => src.ok);
  const model = [payload?.provider, payload?.model].filter(Boolean).join(" · ") || "AI";
  const assetTags = (summary.key_assets || []).filter(Boolean);
  const clusters = coinNewsMapClusters(items);
  const liveRows = items.slice(0, 6).map(item => `
    <div class="news-map-news-row">
      <span class="news-map-source">${{escapeHtml(item.source || "NEWS")}}</span>
      <a href="${{escapeHtml(item.link || "#")}}" target="_blank" rel="noopener noreferrer">${{escapeHtml(item.title || "제목 없음")}}</a>
      <span class="news-map-time">${{escapeHtml(formatCoinNewsAge(item))}}</span>
    </div>
  `).join("");
  const sourceTags = okSources.slice(0, 6).map(src => `${{src.name}} · ${{src.count || 0}}건`);
  canvas.innerHTML = `
    <div class="news-map-layout">
      <div class="news-map-column">
        ${{coinNewsMapList("핵심 요약", summary.brief, "요약 준비 중")}}
        ${{coinNewsMapTags("테마 클러스터", clusters, "분류 대기")}}
      </div>
      <div class="news-map-center">
        <section class="news-map-node center">
          <h3>AI NEWS SUMMARY</h3>
          <div class="news-map-headline">${{escapeHtml(summary.headline || "실시간 뉴스 요약 준비 중")}}</div>
          <div class="news-map-meta">
            <span class="news-map-chip">${{escapeHtml(summary.market_mood || "시장 분위기")}}</span>
            <span class="news-map-chip${{summary.fallback ? " warn" : ""}}">${{summary.fallback ? "FALLBACK" : "AI"}}</span>
            <span class="news-map-chip warn">${{escapeHtml(model)}}</span>
          </div>
          ${{summary.ai_error ? `<div class="ai-summary-error">AI 요약 실패 · ${{escapeHtml(cleanReason(summary.ai_error, 190))}}</div>` : ""}}
        </section>
      </div>
      <div class="news-map-column">
        ${{coinNewsMapList("리스크", summary.risks, "특이 리스크 없음")}}
        ${{coinNewsMapList("다음 체크", summary.watch, "체크포인트 준비 중")}}
      </div>
    </div>
    <div class="news-map-live">
      <section class="news-map-node">
        <h3>실시간 헤드라인</h3>
        ${{liveRows || `<div class="muted">뉴스를 불러오는 중입니다.</div>`}}
      </section>
      <section class="news-map-node">
        <h3>주요 자산</h3>
        <div class="news-map-tags" style="margin-bottom:14px">
          ${{assetTags.length ? assetTags.map(x => `<span class="news-map-tag">${{escapeHtml(x)}}</span>`).join("") : `<span class="news-map-tag muted">자산 분류 대기</span>`}}
        </div>
        <h3>연결 소스</h3>
        <div class="news-map-tags">
          ${{sourceTags.length ? sourceTags.map(x => `<span class="news-map-tag">${{escapeHtml(x)}}</span>`).join("") : `<span class="news-map-tag muted">소스 연결 대기</span>`}}
        </div>
      </section>
    </div>
  `;

  const updated = document.getElementById("coin-news-map-updated");
  if (updated) {{
    const d = new Date(payload?.generated_at || Date.now());
    updated.textContent = isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ko-KR", {{ hour:"2-digit", minute:"2-digit" }});
  }}
  const provider = document.getElementById("coin-news-map-provider");
  if (provider) provider.textContent = summary.fallback ? "FALLBACK" : (payload?.provider || "AI");
  const count = document.getElementById("coin-news-map-count");
  if (count) count.textContent = `${{payload?.news_count || items.length || 0}}건`;
  const sourceTotal = document.getElementById("coin-news-map-sources");
  if (sourceTotal) sourceTotal.textContent = `${{okSources.length}}/${{sources.length}} SOURCES`;
  const sub = document.getElementById("coin-news-map-sub");
  if (sub) sub.textContent = items[0] ? `LATEST ${{formatCoinNewsAge(items[0])}} · LIVE AI NEWS MAP` : "LIVE AI NEWS MAP";
}}

function openCoinNewsMap() {{
  window._coinNewsMapOpen = true;
  const dashboard = document.getElementById("coin-news-dashboard");
  const mapPanel = document.getElementById("coin-news-map-panel");
  const listTools = document.getElementById("coin-news-list-tools");
  if (dashboard) dashboard.hidden = true;
  if (mapPanel) mapPanel.hidden = false;
  if (listTools) listTools.hidden = true;
  renderCoinNewsMap();
  loadCoinNews(true);
  if (window.location.pathname === "/coin" && window.location.search !== "?section=news&view=map") {{
    window.history.replaceState(null, "", "/coin?section=news&view=map");
  }}
  if (mapPanel) mapPanel.scrollIntoView({{ behavior:"smooth", block:"start" }});
}}

function closeCoinNewsMap() {{
  window._coinNewsMapOpen = false;
  const dashboard = document.getElementById("coin-news-dashboard");
  const mapPanel = document.getElementById("coin-news-map-panel");
  const listTools = document.getElementById("coin-news-list-tools");
  if (dashboard) dashboard.hidden = false;
  if (mapPanel) mapPanel.hidden = true;
  if (listTools) listTools.hidden = false;
  if (window.location.pathname === "/coin" && window.location.search.includes("view=map")) {{
    window.history.replaceState(null, "", "/coin?section=news");
  }}
  const newsSection = document.getElementById("coin-news-section");
  if (newsSection) newsSection.scrollIntoView({{ behavior:"smooth", block:"start" }});
}}

function renderCoinNewsSummary(payload) {{
  const box = document.getElementById("coin-news-ai-summary");
  const note = document.getElementById("coin-news-ai-note");
  const updated = document.getElementById("coin-news-ai-updated");
  if (!box) return;
  window._coinNewsSummaryPayload = payload;
  const s = payload?.summary || {{}};
  const tags = (s.key_assets || []).filter(Boolean).map(t => `<span class="ai-summary-tag">${{escapeHtml(t)}}</span>`).join("");
  const model = [payload?.provider, payload?.model].filter(Boolean).join(" · ") || "AI";
  box.innerHTML = `
    <div class="ai-summary-headline">${{escapeHtml(s.headline || "뉴스 요약 없음")}}</div>
    <div class="ai-summary-meta">
      <span class="ai-summary-chip">${{escapeHtml(s.market_mood || "시장 분위기")}}</span>
      <span class="ai-summary-chip${{s.fallback ? " warn" : ""}}">${{s.fallback ? "FALLBACK" : "AI"}}</span>
      <span class="ai-summary-chip warn">${{escapeHtml(model)}}</span>
    </div>
    ${{aiList("핵심 요약", s.brief)}}
    ${{tags ? `<div class="ai-summary-label">주요 자산</div><div class="ai-summary-tags">${{tags}}</div>` : ""}}
    ${{aiList("리스크", s.risks)}}
    ${{aiList("볼 것", s.watch)}}
    ${{s.ai_error ? `<div class="ai-summary-error">AI 요약 실패 · ${{escapeHtml(cleanReason(s.ai_error, 180))}}</div>` : ""}}
  `;
  if (note) {{
    note.textContent = s.source_note || `최신 뉴스 ${{payload?.news_count || 0}}건 기준`;
    note.style.color = s.ai_error ? "#e0b341" : "#5a6577";
  }}
  if (updated && payload?.generated_at) {{
    const d = new Date(payload.generated_at);
    updated.textContent = isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("ko-KR", {{ hour:"2-digit", minute:"2-digit" }});
  }}
  if (window._coinNewsMapOpen) renderCoinNewsMap(payload);
}}

async function loadCoinNewsSummary(force=false) {{
  const note = document.getElementById("coin-news-ai-note");
  const box = document.getElementById("coin-news-ai-summary");
  if (note) {{
    note.textContent = force ? "AI 요약 새로 생성 중입니다." : "AI가 최신 뉴스 요약을 생성 중입니다.";
    note.style.color = "#5a6577";
  }}
  if (box && !window._coinNewsSummaryLoaded) {{
    box.innerHTML = `<div class="muted">AI 요약 생성 중...</div>`;
  }}
  if (window._coinNewsMapOpen) renderCoinNewsMap();
  try {{
    const newsLimit = force || window._coinNewsMapOpen ? 30 : 18;
    const j = await (await fetch(`/api/coin/news_summary?news_limit=${{newsLimit}}` + (force ? "&refresh=1" : ""))).json();
    window._coinNewsSummaryLoaded = true;
    renderCoinNewsSummary(j);
  }} catch(e) {{
    if (note) {{
      note.textContent = e.message || String(e);
      note.style.color = "#ff8a93";
    }}
  }}
}}

async function loadCoinNews(force=false) {{
  const note = document.getElementById("coin-news-note");
  if (note) {{
    note.textContent = force ? "뉴스 새로고침 중입니다." : "뉴스를 불러오는 중입니다.";
    note.style.color = "#5a6577";
  }}
  try {{
    const limit = window._coinNewsMapOpen ? 120 : (force ? 80 : 48);
    const j = await (await fetch(`/api/coin/news?limit=${{limit}}` + (force ? "&refresh=1" : ""))).json();
    window._coinNewsItems = j.items || [];
    window._coinNewsSources = j.sources || [];
    window._coinNewsErrors = j.errors || [];
    window._coinNewsTotalCount = j.total_count || window._coinNewsItems.length;
    window._coinNewsLoaded = true;
    renderCoinNewsSourceFilter();
    renderCoinNewsSources(j);
    renderCoinNews();
    if (window._coinNewsMapOpen) renderCoinNewsMap();
    if (force || !window._coinNewsSummaryLoaded) {{
      clearTimeout(window._coinNewsSummaryTimer);
      window._coinNewsSummaryTimer = setTimeout(() => loadCoinNewsSummary(force), force ? 0 : 450);
    }}
  }} catch(e) {{
    if (note) {{
      note.textContent = e.message || String(e);
      note.style.color = "#ff8a93";
    }}
  }}
}}

async function tickCoinState() {{
  if (document.hidden || window._coinStateLoading) return;
  window._coinStateLoading = true;
  try {{
    const s = await (await fetch("/api/state")).json();
    applyTerminalHeader(s);
    const err = document.getElementById("err");
    if (s.error) {{ err.style.display="block"; err.textContent = "⚠ " + s.error; }}
    else err.style.display="none";
  }} catch(e) {{}}
  finally {{ window._coinStateLoading = false; }}
}}

const coinInitialParams = new URLSearchParams(window.location.search);
const coinInitialSection = coinInitialParams.get("section");
if (["market", "portfolio", "news"].includes(coinInitialSection)) {{
  window._coinSection = coinInitialSection;
}}
if (window.__initialCoinPortfolio) renderCoinPortfolio(window.__initialCoinPortfolio);
loadCoinConfig(); tickTape(); tickCoinState();
setInterval(tickCoinState, 5000);
setInterval(tickTape, 30000);
setInterval(() => {{ if (!window._coinSection || window._coinSection === "market") tickCoinQuote(); }}, 2500);
setInterval(() => {{ if (!window._coinSection || window._coinSection === "market") tickCoinOrderbook(); }}, 4000);
setInterval(() => {{ if (!window._coinSection || window._coinSection === "market") tickCoinChart(); }}, 60000);
setInterval(() => {{ if (!window._coinSection || window._coinSection === "market") loadCoinMarketBoard(); }}, 180000);
setInterval(() => {{ if (window._coinSection === "portfolio") loadCoinPortfolio(); }}, 20000);
setInterval(() => {{ if (window._coinSection === "news") loadCoinNews(window._coinNewsMapOpen); }}, 60000);
if (["market", "portfolio", "news"].includes(coinInitialSection)) {{
  requestAnimationFrame(() => {{
    setCoinSection(coinInitialSection);
    if (coinInitialSection === "news" && coinInitialParams.get("view") === "map") {{
      setTimeout(openCoinNewsMap, 700);
    }}
  }});
}}
</script>
</body></html>"""


# ===== 주식 전용 페이지 =====
STOCKS_HTML = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stockagent · 주식</title>
{FONTS_HEAD}
<style>{BASE_CSS}</style>
</head><body>

<div class="hd">
  <div class="hd-row">
    <span class="brand" id="brand">stockagent<span class="cursor">_</span></span>
    <span id="mode-pill" class="pill-live"><span class="dot"></span><span>LIVE · 실거래</span></span>
    <span class="pill-ai" id="ai-pill">AI - · -</span>
    <span class="pill-bot" id="bot-pill">BOT 대기</span>
    <div class="head-tools">
      <button class="mini-btn danger" id="pause-bot" onclick="setBotPaused(true)">일시정지</button>
      <button class="mini-btn" id="resume-bot" onclick="setBotPaused(false)">재개</button>
    </div>
    <span style="flex:1"></span>
    <div class="tabs">
      {_nav_tabs("stocks")}
    </div>
    <span class="upd" id="upd">UPD —</span>
  </div>
  <div class="tape-wrap">
    <div class="tape-row" id="tape"></div>
  </div>
</div>

<div class="wrap">
  <div class="err-banner" id="err"></div>

  <div class="section-line">
    <div class="section-title">주식 마켓</div>
    <div class="market-actions">
      <select class="terminal-select" id="stock-preset" onchange="setStockPreset(this.value)"></select>
      <input id="stock-code" value="005930" autocomplete="off" inputmode="numeric">
      <button class="mini-btn" onclick="loadStock()">조회</button>
      <button class="mini-btn on" data-stock-frame="day" onclick="setStockFrame('day')">일봉</button>
      <button class="mini-btn" data-stock-frame="week" onclick="setStockFrame('week')">주봉</button>
      <button class="mini-btn" data-stock-frame="month" onclick="setStockFrame('month')">월봉</button>
    </div>
  </div>

  <div class="market-stat-grid">
    <div class="market-stat"><div class="label">현재가</div><div class="val" id="stock-price">—</div></div>
    <div class="market-stat"><div class="label">등락</div><div class="val" id="stock-change">—</div></div>
    <div class="market-stat"><div class="label">상태</div><div class="val" id="stock-status">—</div></div>
    <div class="market-stat"><div class="label">RSI14</div><div class="val" id="stock-rsi">—</div></div>
  </div>

  <div class="market-grid">
    <div class="box">
      <div class="box-head">
        <span id="stock-chart-title">— · —</span>
        <span style="flex:1"></span>
        <span class="legend"><span class="swatch" style="background:#1fd6a8"></span>종가</span>
        <span class="legend"><span class="swatch" style="background:#e0b341"></span>MA5</span>
        <span class="legend"><span class="swatch" style="background:#5a6577"></span>MA20</span>
      </div>
      <div style="padding:8px 8px 0">
        <svg id="stockSvg" viewBox="0 0 1000 260" preserveAspectRatio="none"
             style="width:100%;height:360px;display:block"></svg>
      </div>
    </div>

    <div class="box">
      <div class="box-head">
        <span>HOGA / QUOTE</span>
        <span class="total" id="stock-updated">—</span>
      </div>
      <div id="stock-quote-rows"></div>
      <div class="metric-list" id="stock-metric-rows"></div>
      <div class="report-note" id="stock-flow">—</div>
    </div>
  </div>

  <div class="foot" id="foot">—</div>
</div>

<script>
{COMMON_JS}

const stockPresets = {json.dumps(STOCK_PRESETS, ensure_ascii=False)};
const stockFrameLabel = {{ day:"일봉", week:"주봉", month:"월봉" }};
window._stockFrame = "day";

function setupStockPresets() {{
  const sel = document.getElementById("stock-preset");
  sel.innerHTML = stockPresets.map(s => `<option value="${{s.code}}">${{s.name}} · ${{s.code}}</option>`).join("");
  sel.value = "005930";
}}

function setStockPreset(code) {{
  document.getElementById("stock-code").value = code || "005930";
  loadStock();
}}

function setStockFrame(frame) {{
  window._stockFrame = frame;
  document.querySelectorAll("[data-stock-frame]").forEach(b => b.classList.toggle("on", b.dataset.stockFrame === frame));
  loadStock();
}}

function stockCode() {{
  return (document.getElementById("stock-code").value || "005930").replace(/\\D/g, "").slice(0, 6) || "005930";
}}

function renderStockQuote(q) {{
  const change = Number(q.change || 0);
  const pct = q.change_pct == null ? null : Number(q.change_pct);
  document.getElementById("stock-price").textContent = q.price_text ? q.price_text + " 원" : KRW(q.price) + " 원";
  document.getElementById("stock-change").textContent =
    (q.change_text || KRW(change, true)) + (pct == null ? "" : ` (${{PCT(pct)}})`);
  document.getElementById("stock-change").className = "val " + colorOf(change);
  document.getElementById("stock-status").textContent = q.status || "—";
  document.getElementById("stock-updated").textContent = q.updated_at || "—";
  document.getElementById("stock-quote-rows").innerHTML = (q.rows || []).map(r => `
    <div class="quote-row">
      <span class="k">${{r.label || "—"}}</span>
      <span class="v">${{r.value || "—"}}</span>
    </div>
  `).join("");
  document.getElementById("stock-metric-rows").innerHTML = (q.metrics || []).map(r => `
    <div class="quote-row">
      <span class="k">${{r.label || "—"}}</span>
      <span class="v">${{r.value || "—"}}</span>
    </div>
  `).join("");
  const latest = (q.deal_trends || [])[0] || {{}};
  document.getElementById("stock-flow").textContent =
    latest.bizdate ? `개인 ${{latest.individualPureBuyQuant || "—"}} · 외국인 ${{latest.foreignerPureBuyQuant || "—"}} · 기관 ${{latest.organPureBuyQuant || "—"}}` : "—";
}}

async function loadStock() {{
  const code = stockCode();
  document.getElementById("stock-code").value = code;
  try {{
    const quoteRes = await fetch(`/api/stocks/quote?code=${{code}}`);
    const quote = await quoteRes.json();
    if (!quoteRes.ok) throw new Error(quote.error || "주식 시세 조회 실패");
    renderStockQuote(quote);

    const chartRes = await fetch(`/api/stocks/candles?code=${{code}}&timeframe=${{window._stockFrame}}&count=140`);
    const chart = await chartRes.json();
    if (!chartRes.ok) throw new Error(chart.error || "주식 차트 조회 실패");
    const stats = renderChart("stockSvg", null, chart);
    document.getElementById("stock-chart-title").textContent =
      `${{quote.name || code}} · ${{code}} · ${{stockFrameLabel[window._stockFrame]}}`;
    document.getElementById("stock-rsi").textContent = stats.lastRsi != null ? stats.lastRsi.toFixed(1) : "—";
  }} catch(e) {{
    const err = document.getElementById("err");
    err.style.display = "block";
    err.textContent = e.message || e;
  }}
}}

async function tickStockState() {{
  try {{
    const s = await (await fetch("/api/state")).json();
    applyTerminalHeader(s);
  }} catch(e) {{}}
}}

setupStockPresets(); loadStock(); tickTape(); tickStockState();
setInterval(tickTape, 15000);
setInterval(tickStockState, 3000);
setInterval(loadStock, 30000);
</script>
</body></html>"""


# ===== 대시보드 페이지 =====
DASHBOARD_HTML = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stockagent · Terminal</title>
{FONTS_HEAD}
<style>{BASE_CSS}</style>
</head><body>

<div class="hd">
  <div class="hd-row">
    <span class="brand" id="brand">stockagent<span class="cursor">_</span></span>
    <span id="mode-pill" class="pill-live"><span class="dot"></span><span id="mode-text">LIVE · 실거래</span></span>
    <span class="pill-ai" id="ai-pill">AI - · -</span>
    <span class="pill-bot" id="bot-pill">BOT 대기</span>
    <div class="head-tools">
      <button class="mini-btn danger" id="pause-bot" onclick="setBotPaused(true)">일시정지</button>
      <button class="mini-btn" id="resume-bot" onclick="setBotPaused(false)">재개</button>
    </div>
    <span style="flex:1"></span>
    <div class="tabs">
      {_nav_tabs("assets")}
    </div>
    <span class="upd" id="upd">UPD —</span>
  </div>
  <div class="tape-wrap">
    <div class="tape-row" id="tape"></div>
  </div>
</div>

<div class="wrap">
  <div class="err-banner" id="err"></div>

  <div class="section-title">손익 요약</div>
  <div class="kpi-grid">
    <div class="kpi"><div class="label">오늘 실현손익</div><div class="val" id="kpi-today">—</div></div>
    <div class="kpi"><div class="label">누적 실현손익</div><div class="val" id="kpi-total">—</div></div>
    <div class="kpi"><div class="label">원화 잔고</div>     <div class="val" id="kpi-krw">—</div></div>
    <div class="kpi"><div class="label">총 평가금액</div>   <div class="val" id="kpi-port">—</div></div>
    <div class="kpi"><div class="label">오늘 매매</div>     <div class="val" id="kpi-trades">—</div></div>
    <div class="kpi"><div class="label">승률</div>          <div class="val" id="kpi-win">—</div></div>
  </div>

  <div class="row-grid">

    <div class="box">
      <div class="box-head">
        <span>PORTFOLIO</span>
        <span class="total" id="port-total">—</span>
      </div>
      <div class="tbl-head port-head">
        <span>자산</span>
        <span style="text-align:right">평가</span>
        <span style="text-align:right">비중</span>
        <span style="text-align:right">수익률</span>
      </div>
      <div id="port-rows"></div>
    </div>

    <div class="box">
      <div class="box-head">
        <span id="chart-title">— · —</span>
        <div class="chart-controls">
          <select class="terminal-select" id="chart-ticker" onchange="setChartTicker(this.value)"></select>
          <button class="mini-btn" data-chart-iv="minute15" onclick="setChartInterval('minute15')">15분</button>
          <button class="mini-btn on" data-chart-iv="minute60" onclick="setChartInterval('minute60')">1시간</button>
          <button class="mini-btn" data-chart-iv="day" onclick="setChartInterval('day')">일봉</button>
        </div>
        <span style="flex:1"></span>
        <span class="legend"><span class="swatch" style="background:#1fd6a8"></span>종가</span>
        <span class="legend"><span class="swatch" style="background:#e0b341"></span>MA5</span>
        <span class="legend"><span class="swatch" style="background:#5a6577"></span>MA20</span>
      </div>
      <div style="padding:8px 8px 0">
        <svg id="priceSvg" viewBox="0 0 1000 260" preserveAspectRatio="none"
             style="width:100%;height:228px;display:block"></svg>
      </div>
      <div style="display:flex;justify-content:space-between;padding:9px 15px 11px;font-size:11px;color:#5a6577;border-top:1px solid #141a23;margin-top:4px">
        <span>현재가 <span id="chart-price" style="color:#e6ebf2;font-weight:600">—</span> <span id="chart-chg">—</span></span>
        <span>RSI14 <span id="chart-rsi" style="color:#e0b341;font-weight:600">—</span></span>
      </div>
    </div>

  </div>

  <div class="section-line">
    <div class="section-title">내 포트폴리오 · 상세</div>
    <div class="inline-tools">
      <button class="mini-btn" onclick="loadPortfolioDetail()">새로고침</button>
    </div>
  </div>
  <div class="portfolio-detail">
    <div class="box">
      <div class="box-head">
        <span>HOLDINGS</span>
        <span class="total" id="portfolio-detail-total">—</span>
      </div>
      <div class="portfolio-metrics">
        <div class="portfolio-metric"><div class="label">총 원금</div><div class="val" id="pf-principal">—</div></div>
        <div class="portfolio-metric"><div class="label">미실현손익</div><div class="val" id="pf-unrealized">—</div></div>
        <div class="portfolio-metric"><div class="label">실현손익</div><div class="val" id="pf-realized">—</div></div>
        <div class="portfolio-metric"><div class="label">현금 비중</div><div class="val" id="pf-cash-ratio">—</div></div>
      </div>
      <div class="tbl-head holding-head">
        <span>자산</span><span style="text-align:right">평가 / 원금</span><span style="text-align:right">평단</span>
        <span style="text-align:right">미실현</span><span style="text-align:right">실현</span><span style="text-align:right">거래</span>
      </div>
      <div id="holding-rows"></div>
    </div>

    <div class="box">
      <div class="box-head">
        <span>ALLOCATION</span>
        <span class="total" id="pf-risk-note">—</span>
      </div>
      <div id="allocation-rows"></div>
      <div class="spark-wrap">
        <svg id="pnlSpark" viewBox="0 0 1000 90" preserveAspectRatio="none" style="width:100%;height:36px;display:block"></svg>
      </div>
      <div class="report-note" id="pf-risk-text">포트폴리오 상세를 불러오는 중입니다.</div>
      <div class="manual-panel">
        <div class="tbl-head">MANUAL INPUT</div>
        <div class="manual-form">
          <input id="manual-ticker" autocomplete="off" placeholder="KRW-BTC / BTC / KRW">
          <input id="manual-balance" autocomplete="off" inputmode="decimal" placeholder="수량 또는 원화">
          <input id="manual-avg" autocomplete="off" inputmode="decimal" placeholder="평단">
          <button class="mini-btn" onclick="saveManualPortfolio()">저장</button>
          <button class="mini-btn danger" onclick="deleteManualPortfolio()">삭제</button>
        </div>
        <div class="manual-status" id="manual-status"></div>
        <div class="manual-rows" id="manual-rows"></div>
      </div>
    </div>
  </div>

  <div class="section-line">
    <div class="section-title">AI 판단 기록 · 최신순</div>
    <div class="inline-tools">
      <select class="terminal-select" id="hist-ticker" onchange="tickHistory()"><option value="">전체 종목</option></select>
      <select class="terminal-select" id="hist-action" onchange="tickHistory()">
        <option value="">전체 판단</option><option>BUY</option><option>SELL</option><option>HOLD</option>
      </select>
      <a class="mini-btn" href="/api/export/decisions.csv">CSV</a>
    </div>
  </div>
  <div class="box" style="margin-bottom:24px">
    <div class="tbl-head hist-head">
      <span>시각</span><span>종목</span>
      <span style="text-align:right">가격</span>
      <span style="padding-left:14px">근거</span>
      <span style="text-align:center">RSI</span>
      <span style="text-align:center">판단</span>
      <span style="text-align:right">신뢰</span>
      <span style="text-align:right">주문</span>
    </div>
    <div id="hist-rows"></div>
  </div>

  <div class="section-line">
    <div class="section-title">최근 거래 · DB</div>
    <div class="inline-tools">
      <select class="terminal-select" id="trade-ticker" onchange="tickPnl()"><option value="">전체 종목</option></select>
      <select class="terminal-select" id="trade-side" onchange="tickPnl()">
        <option value="">전체 SIDE</option><option value="buy">buy</option><option value="sell">sell</option>
      </select>
      <a class="mini-btn" href="/api/export/trades.csv">CSV</a>
    </div>
  </div>
  <div class="box">
    <div class="tbl-head trade-head">
      <span>시각</span><span>종목</span><span>side</span>
      <span style="text-align:right">가격</span>
      <span style="text-align:right">수량</span>
      <span style="text-align:right">원화</span>
      <span style="text-align:right">수수료</span>
      <span style="text-align:right">실현손익</span>
      <span style="text-align:right">모드</span>
    </div>
    <div id="trade-rows"></div>
  </div>

  <div class="foot" id="foot">—</div>
</div>

<script>
{COMMON_JS}

const dashboardIntervalLabel = {{ "minute15":"15분", "minute60":"1시간", "day":"일봉" }};
window._chartTicker = "KRW-BTC";
window._chartInterval = "minute60";

function tickerOptions(tickers, includeAllLabel) {{
  const opts = includeAllLabel ? [`<option value="">${{includeAllLabel}}</option>`] : [];
  for (const t of tickers) opts.push(`<option value="${{t}}">${{t}}</option>`);
  return opts.join("");
}}

async function loadDashboardConfig() {{
  try {{
    const cfg = await (await fetch("/api/config")).json();
    const tickers = cfg.tickers && cfg.tickers.length ? cfg.tickers : ["KRW-BTC"];
    window._chartTicker = tickers[0];
    const chartSel = document.getElementById("chart-ticker");
    chartSel.innerHTML = tickerOptions(tickers, "");
    chartSel.value = window._chartTicker;
    document.getElementById("hist-ticker").innerHTML = tickerOptions(tickers, "전체 종목");
    document.getElementById("trade-ticker").innerHTML = tickerOptions(tickers, "전체 종목");
    tickChart();
  }} catch(e) {{}}
}}

function setChartTicker(ticker) {{
  window._chartTicker = ticker || "KRW-BTC";
  tickChart();
}}

function setChartInterval(interval) {{
  window._chartInterval = interval;
  document.querySelectorAll("[data-chart-iv]").forEach(b => b.classList.toggle("on", b.dataset.chartIv === interval));
  tickChart();
}}

function renderHistoryItems(items) {{
  document.getElementById("hist-rows").innerHTML = (items || []).map(h => {{
    const time = h.time || (h.ts ? h.ts.replace("T", " ").slice(11, 19) : "—");
    const action = h.action || "HOLD";
    const conf = Number(h.confidence || 0);
    const order = h.order || `${{h.order_side || "skip"}} | ${{h.order_reason || ""}}`;
    return `
      <div class="tbl-row hist-grid">
        <span class="muted">${{time}}</span>
        <span style="color:#cdd5e0;font-weight:600">${{h.ticker || "—"}}</span>
        <span style="text-align:right;color:#9aa3b5">${{KRW(h.price)}}</span>
        <span class="reason-text" title="${{escapeHtml(cleanReason(h.reasoning, 500))}}">${{escapeHtml(cleanReason(h.reasoning))}}</span>
        <span style="text-align:center;color:#8a95a8">${{h.rsi != null ? h.rsi : "—"}}</span>
        <span style="text-align:center"><span class="action-chip act-${{action}}">${{action}}</span></span>
        <span style="text-align:right;font-weight:600;color:${{conf>=0.6?"#e6ebf2":"#5a6577"}}">${{conf.toFixed(2)}}</span>
        <span style="text-align:right;color:#5a6577;font-size:10.5px">${{order}}</span>
      </div>`;
  }}).join("") || `<div class="tbl-row muted">아직 판단 없음</div>`;
}}

function renderTradeItems(items) {{
  document.getElementById("trade-rows").innerHTML = (items || []).map(t => {{
    const sideCls = t.side === "buy" ? "up" : "down";
    const pnl = t.realized_pnl || 0;
    const pnlCls = pnl>0 ? "up" : pnl<0 ? "down" : "muted";
    const pnlStr = pnl===0 ? "—" : (pnl>0?"+":"") + KRW(pnl);
    return `
      <div class="tbl-row trade-grid">
        <span class="muted">${{t.ts.replace("T"," ")}}</span>
        <span style="color:#cdd5e0;font-weight:600">${{t.ticker}}</span>
        <span class="${{sideCls}}" style="font-weight:700;font-size:10.5px">${{t.side}}</span>
        <span style="text-align:right;color:#9aa3b5">${{KRW(t.price)}}</span>
        <span style="text-align:right;color:#9aa3b5">${{NUM(t.volume)}}</span>
        <span style="text-align:right">${{KRW(t.krw_amount)}}</span>
        <span style="text-align:right" class="muted">${{KRW(t.fee)}}</span>
        <span style="text-align:right;font-weight:600" class="${{pnlCls}}">${{pnlStr}}</span>
        <span style="text-align:right;font-size:10px" class="muted">${{t.dry_run?"DRY":"LIVE"}}</span>
      </div>`;
  }}).join("") || `<div class="tbl-row muted">아직 거래 없음</div>`;
}}

function renderPnlSpark(daily) {{
  const svg = document.getElementById("pnlSpark");
  if (!svg) return;
  const vals = (daily || []).slice().reverse().map(d => Number(d.realized_pnl || 0));
  if (!vals.length) {{
    svg.innerHTML = `<text x="10" y="48" fill="#3a4658" font-size="28">NO DAILY PNL</text>`;
    return;
  }}
  let run = 0;
  const cumulative = vals.map(v => (run += v));
  const lo = Math.min(...cumulative), hi = Math.max(...cumulative);
  const pad = Math.max(1, (hi - lo) * 0.2);
  const path = buildPath(cumulative, 1000, 90, lo - pad, hi + pad, 8);
  const stroke = cumulative[cumulative.length - 1] >= 0 ? "#1fd6a8" : "#ff5d6c";
  svg.innerHTML = `
    <line x1="0" y1="45" x2="1000" y2="45" stroke="#141a23" stroke-width="1" vector-effect="non-scaling-stroke"></line>
    <path d="${{path}}" fill="none" stroke="${{stroke}}" stroke-width="2" vector-effect="non-scaling-stroke"></path>`;
}}

function setManualStatus(text, isError=false) {{
  const el = document.getElementById("manual-status");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = isError ? "#ff5d6c" : "#5a6577";
}}

function fillManualFromRow(row) {{
  document.getElementById("manual-ticker").value = row.dataset.manualTicker || "";
  document.getElementById("manual-balance").value = row.dataset.manualBalance || "";
  document.getElementById("manual-avg").value = row.dataset.manualAvg || "";
  setManualStatus("");
}}

function renderManualRows(items) {{
  const rows = document.getElementById("manual-rows");
  if (!rows) return;
  rows.innerHTML = (items || []).map(m => {{
    const ticker = m.ticker || "";
    const isCash = ticker === "KRW";
    const balance = Number(m.balance || 0);
    const avg = Number(m.avg_buy_price || 0);
    const balanceText = isCash ? KRW(balance) + " 원" : NUM(balance);
    const avgText = isCash ? "현금" : "평단 " + KRW(avg);
    return `
      <div class="manual-row" onclick="fillManualFromRow(this)"
           data-manual-ticker="${{escapeHtml(ticker)}}"
           data-manual-balance="${{balance}}"
           data-manual-avg="${{avg}}">
        <span style="font-weight:700;color:#cdd5e0">${{escapeHtml(ticker)}}</span>
        <span style="text-align:right;color:#9aa3b5">${{balanceText}}</span>
        <span class="muted" style="text-align:right">${{avgText}}</span>
      </div>`;
  }}).join("") || `<div class="tbl-row muted">직접 입력된 보유 정보 없음</div>`;
}}

function manualNumberValue(id) {{
  return document.getElementById(id).value.replace(/,/g, "").trim();
}}

async function saveManualPortfolio() {{
  try {{
    const ticker = document.getElementById("manual-ticker").value.trim();
    const balance = manualNumberValue("manual-balance");
    const avg = manualNumberValue("manual-avg");
    const r = await fetch("/api/manual_portfolio", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{ ticker, balance, avg_buy_price: avg }}),
    }});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "저장 실패");
    setManualStatus("저장됨 · " + (j.item && j.item.ticker ? j.item.ticker : ticker));
    renderPortfolioDetail(j.portfolio);
  }} catch(e) {{
    setManualStatus(e.message || String(e), true);
  }}
}}

async function deleteManualPortfolio() {{
  try {{
    const ticker = document.getElementById("manual-ticker").value.trim();
    if (!ticker) throw new Error("삭제할 종목을 입력하세요");
    if (!confirm(ticker + " 입력값을 삭제할까요?")) return;
    const r = await fetch("/api/manual_portfolio", {{
      method: "DELETE",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{ ticker }}),
    }});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "삭제 실패");
    setManualStatus(j.deleted ? "삭제됨" : "삭제할 항목 없음");
    renderPortfolioDetail(j.portfolio);
  }} catch(e) {{
    setManualStatus(e.message || String(e), true);
  }}
}}

function detailHoldingsToCompact(pf) {{
  const holdings = (pf && pf.holdings) || [];
  const summary = (pf && pf.summary) || {{}};
  const total = Number(summary.total_value || 0);
  return holdings.map(h => {{
    const currentValue = Number(h.current_value || 0);
    const weight = h.weight != null ? Number(h.weight) : (total > 0 ? currentValue / total * 100 : 0);
    return {{
      currency: h.currency,
      balance: h.balance,
      current_value: currentValue,
      weight,
      return_pct: h.currency === "KRW" ? null : h.return_pct,
    }};
  }});
}}

function renderCompactPortfolio(items, portVal) {{
  const hasValue = portVal != null && !isNaN(Number(portVal));
  const totalValue = hasValue ? Number(portVal) : 0;
  document.getElementById("port-total").textContent = hasValue ? (KRW(portVal) + " 원") : "—";
  document.getElementById("port-rows").innerHTML = (items || []).map(p => {{
    const currentValue = Number(p.current_value || 0);
    const weight = p.weight != null ? Number(p.weight) : (totalValue > 0 ? currentValue / totalValue * 100 : 0);
    const ret = p.currency === "KRW" ? null : p.return_pct;
    const retStr = ret==null ? "—" : PCT(Number(ret));
    const retCls = ret==null ? "muted" : colorOf(Number(ret));
    return `
      <div class="tbl-row port-grid">
        <div>
          <div style="font-weight:700;font-size:13px">${{p.currency || "—"}}</div>
          <div style="font-size:10px;color:#5a6577;margin-top:2px">${{NUM(p.balance)}}</div>
        </div>
        <div style="text-align:right;font-size:12.5px">${{KRW(currentValue)}}</div>
        <div style="text-align:right">
          <div style="font-size:11px;color:#8a95a8;margin-bottom:5px">${{weight.toFixed(1)}}%</div>
          <div class="port-bar"><span style="width:${{Math.min(100, weight)}}%"></span></div>
        </div>
        <div style="text-align:right;font-size:12px;font-weight:600" class="${{retCls}}">${{retStr}}</div>
      </div>`;
  }}).join("") || `<div class="tbl-row muted">보유 자산 없음</div>`;
}}

function renderPortfolioDetail(pf) {{
  const s = pf.summary || {{}};
  const holdings = pf.holdings || [];
  window._lastPortfolioPayload = pf;
  document.getElementById("portfolio-detail-total").textContent = KRW(s.total_value) + " 원";
  document.getElementById("pf-principal").textContent = KRW(s.total_principal) + " 원";
  const unrealizedCls = colorOf(s.unrealized_pnl || 0);
  const realizedCls = colorOf(s.realized_pnl || 0);
  document.getElementById("pf-unrealized").textContent = KRW(s.unrealized_pnl, true) + " 원";
  document.getElementById("pf-unrealized").className = "val " + unrealizedCls;
  document.getElementById("pf-realized").textContent = KRW(s.realized_pnl, true) + " 원";
  document.getElementById("pf-realized").className = "val " + realizedCls;
  document.getElementById("pf-cash-ratio").textContent = (s.cash_ratio || 0).toFixed(1) + "%";
  document.getElementById("kpi-krw").textContent = KRW(s.cash_value) + " 원";
  document.getElementById("kpi-port").textContent = KRW(s.total_value) + " 원";
  renderCompactPortfolio(detailHoldingsToCompact(pf), s.total_value);
  renderManualRows(pf.manual_items || []);
  document.getElementById("holding-rows").innerHTML = holdings.map(h => {{
    const isCash = h.currency === "KRW";
    const unrealized = Number(h.unrealized_pnl || 0);
    const realized = Number(h.realized_pnl || 0);
    return `
      <div class="tbl-row holding-row">
        <div>
          <div style="font-weight:700;font-size:13px">${{h.currency || "—"}}</div>
          <div class="muted" style="font-size:10px;margin-top:2px">${{isCash ? "현금" : NUM(h.balance)}}</div>
        </div>
        <div style="text-align:right">
          <div>${{KRW(h.current_value)}} 원</div>
          <div class="muted" style="font-size:10px;margin-top:2px">원금 ${{KRW(h.principal)}}</div>
        </div>
        <div style="text-align:right;color:#9aa3b5">${{isCash ? "—" : KRW(h.avg_buy_price)}}</div>
        <div style="text-align:right;font-weight:600" class="${{colorOf(unrealized)}}">${{isCash ? "—" : KRW(unrealized, true)}}</div>
        <div style="text-align:right;font-weight:600" class="${{colorOf(realized)}}">${{KRW(realized, true)}}</div>
        <div style="text-align:right;color:#8a95a8">${{h.trades_count || 0}}</div>
      </div>`;
  }}).join("") || `<div class="tbl-row muted">포트폴리오 데이터 없음</div>`;

  document.getElementById("allocation-rows").innerHTML = holdings.map(h => `
    <div class="allocation-row">
      <span style="font-weight:700;color:#cdd5e0">${{h.currency || "—"}}</span>
      <div class="port-bar"><span style="width:${{Math.min(100, Number(h.weight || 0))}}%"></span></div>
      <span style="text-align:right;color:#8a95a8">${{Number(h.weight || 0).toFixed(1)}}%</span>
    </div>
  `).join("") || `<div class="tbl-row muted">배분 데이터 없음</div>`;

  const risk = pf.risk || {{}};
  document.getElementById("pf-risk-note").textContent = s.largest_asset ? `${{s.largest_asset}} ${{Number(s.largest_weight || 0).toFixed(1)}}%` : "—";
  const riskText = document.getElementById("pf-risk-text");
  if (pf.account_error) {{
    riskText.textContent = "업비트 계좌 조회 실패 · " + pf.account_error;
    riskText.style.color = "#ff8a93";
  }} else {{
    riskText.textContent =
      `최대 주문 ${{KRW(risk.max_order_krw)}}원 · 일 손실한도 ${{KRW(risk.max_daily_loss_krw)}}원 · 최소 신뢰도 ${{risk.min_confidence}}`;
    riskText.style.color = "#3a4658";
  }}
  renderPnlSpark(pf.daily || []);
}}

async function loadPortfolioDetail() {{
  try {{
    const pf = await (await fetch("/api/portfolio")).json();
    renderPortfolioDetail(pf);
  }} catch(e) {{}}
}}

async function tickHistory() {{
  try {{
    const ticker = document.getElementById("hist-ticker").value;
    const action = document.getElementById("hist-action").value;
    const qs = new URLSearchParams({{ limit: "100" }});
    if (ticker) qs.set("ticker", ticker);
    if (action) qs.set("action", action);
    const j = await (await fetch("/api/decisions?" + qs.toString())).json();
    renderHistoryItems(j.items || []);
  }} catch(e) {{}}
}}

// 헤더 모드 / AI 모델 / UPD
function applyState(s) {{
  applyTerminalHeader(s);

  // KPI
  setPnl("kpi-today", s.today_pnl);
  setPnl("kpi-total", s.total_pnl);
  let krwBalance = s.krw_balance;
  let items = (s.portfolio && s.portfolio.items) || [];
  let portVal = s.portfolio && s.portfolio.total_value;
  if ((!items || !items.length) && window._lastPortfolioPayload) {{
    const summary = window._lastPortfolioPayload.summary || {{}};
    items = detailHoldingsToCompact(window._lastPortfolioPayload);
    portVal = summary.total_value;
    krwBalance = summary.cash_value;
  }}
  document.getElementById("kpi-krw").textContent =
    krwBalance != null && !isNaN(Number(krwBalance)) ? (KRW(krwBalance) + " 원") : "—";
  document.getElementById("kpi-port").textContent =
    portVal != null && !isNaN(Number(portVal)) ? (KRW(portVal) + " 원") : "—";
  renderCompactPortfolio(items, portVal);

  // 판단 기록
  if (!document.getElementById("hist-ticker").value && !document.getElementById("hist-action").value) {{
    renderHistoryItems(s.history || []);
  }}

  // 에러
  const err = document.getElementById("err");
  if (s.error) {{ err.style.display="block"; err.textContent = "⚠ " + s.error; }}
  else err.style.display="none";

  if (!window._chartTicker && s.tickers && s.tickers[0]) window._chartTicker = s.tickers[0].ticker;
}}

function setPnl(id, v) {{
  const el = document.getElementById(id);
  el.textContent = (v>0?"+":"") + KRW(v) + " 원";
  el.className = "val " + (v>0?"up":v<0?"down":"");
}}

async function tickState() {{
  try {{
    const s = await (await fetch("/api/state")).json();
    applyState(s);
  }} catch(e) {{}}
}}

async function tickPnl() {{
  try {{
    const j = await (await fetch("/api/pnl")).json();
    document.getElementById("kpi-trades").textContent = (j.today_trades || 0) + " 회";
    document.getElementById("kpi-win").textContent = (j.win_rate || 0).toFixed(0) + " %";
    const qs = new URLSearchParams({{ limit: "100" }});
    const ticker = document.getElementById("trade-ticker").value;
    const side = document.getElementById("trade-side").value;
    if (ticker) qs.set("ticker", ticker);
    if (side) qs.set("side", side);
    const trades = await (await fetch("/api/trades?" + qs.toString())).json();
    renderTradeItems(trades.items || j.trades || []);
  }} catch(e) {{}}
}}

async function tickTape() {{
  await loadTickerTape();
}}

async function tickChart() {{
  try {{
    const ticker = window._chartTicker || "KRW-BTC";
    const interval = window._chartInterval || "minute60";
    const j = await (await fetch(`/api/candles?ticker=${{ticker}}&interval=${{interval}}&count=120`)).json();
    if (j.error) return;
    const stats = renderChart("priceSvg", null, j);
    document.getElementById("chart-title").textContent = ticker + " · " + dashboardIntervalLabel[interval];
    document.getElementById("chart-price").textContent = KRW(stats.lastClose);
    const chg = stats.changePct;
    const chgEl = document.getElementById("chart-chg");
    chgEl.textContent = PCT(chg);
    chgEl.className = colorOf(chg);
    document.getElementById("chart-rsi").textContent = stats.lastRsi != null ? stats.lastRsi.toFixed(1) : "—";
  }} catch(e) {{}}
}}

// 첫 호출
loadDashboardConfig(); tickState(); tickPnl(); tickTape(); tickHistory(); loadPortfolioDetail();
setInterval(tickState, 3000);
setInterval(tickPnl, 10000);
setInterval(tickTape, 15000);
setInterval(tickChart, 30000);
setInterval(tickHistory, 10000);
setInterval(loadPortfolioDetail, 15000);
</script>
</body></html>"""


# ===== 분석 페이지 =====
ANALYZE_HTML = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stockagent · 종목 분석</title>
{FONTS_HEAD}
<style>{BASE_CSS}</style>
</head><body>

<div class="hd">
  <div class="hd-row">
    <span class="brand" id="brand">stockagent<span class="cursor">_</span></span>
    <span id="mode-pill" class="pill-live"><span class="dot"></span><span>LIVE · 실거래</span></span>
    <span class="pill-ai" id="ai-pill">AI - · -</span>
    <span class="pill-bot" id="bot-pill">BOT 대기</span>
    <div class="head-tools">
      <button class="mini-btn danger" id="pause-bot" onclick="setBotPaused(true)">일시정지</button>
      <button class="mini-btn" id="resume-bot" onclick="setBotPaused(false)">재개</button>
    </div>
    <span style="flex:1"></span>
    <div class="tabs">
      {_nav_tabs("analyze")}
    </div>
    <span class="upd" id="upd">UPD —</span>
  </div>
  <div class="tape-wrap">
    <div class="tape-row" id="tape"></div>
  </div>
</div>

<div class="wrap">

  <div class="search-bar">
    <span style="font-size:11px;color:#e0b341;letter-spacing:.06em;text-transform:uppercase;margin-right:6px">종목 분석</span>
    <input id="ticker-input" placeholder="KRW-BTC" value="KRW-BTC"
           onkeydown="if(event.key==='Enter') run()">
    <div class="iv-group" id="iv-group">
      <button class="iv-btn" data-iv="minute15">15분</button>
      <button class="iv-btn on" data-iv="minute60">1시간</button>
      <button class="iv-btn" data-iv="day">일봉</button>
    </div>
    <button class="run-btn" onclick="run()">분석 실행</button>
    <span style="flex:1"></span>
    <span style="font-size:11px;color:#5a6577">분석 대상 <span id="sel-ticker" style="color:#cdd5e0;font-weight:600">—</span></span>
  </div>
  <div class="inline-tools" id="analysis-presets" style="margin:-10px 0 18px"></div>

  <div class="snap-grid">
    <div class="snap-card"><div class="label">현재가</div><div class="val" id="s-price">—</div><div class="sub" id="s-price-sub">—</div></div>
    <div class="snap-card"><div class="label">RSI14</div><div class="val" id="s-rsi">—</div><div class="sub" id="s-rsi-sub">—</div></div>
    <div class="snap-card"><div class="label">MA5 / MA20</div><div class="val" id="s-ma">—</div><div class="sub" id="s-ma-sub">—</div></div>
    <div class="snap-card"><div class="label">구간 변동률</div><div class="val" id="s-chg">—</div><div class="sub">120 candles</div></div>
  </div>

  <div class="analyze-grid">
    <div class="box">
      <div class="box-head">
        <span id="chart-title">— · —</span>
        <span style="flex:1"></span>
        <span class="legend"><span class="swatch" style="background:#1fd6a8"></span>종가</span>
        <span class="legend"><span class="swatch" style="background:#e0b341"></span>MA5</span>
        <span class="legend"><span class="swatch" style="background:#5a6577"></span>MA20</span>
      </div>
      <div style="padding:8px 8px 0">
        <svg id="priceSvg" viewBox="0 0 1000 260" preserveAspectRatio="none"
             style="width:100%;height:230px;display:block"></svg>
      </div>
      <div style="padding:6px 15px 4px;font-size:10.5px;color:#5aa3ff;border-top:1px solid #141a23;margin-top:4px">
        RSI14 <span id="chart-rsi">—</span>
      </div>
      <div style="padding:0 8px 10px">
        <svg id="rsiSvg" viewBox="0 0 1000 90" preserveAspectRatio="none"
             style="width:100%;height:62px;display:block"></svg>
      </div>
    </div>

    <div class="box">
      <div class="box-head">
        <span>AI 분석 리포트</span>
        <span style="color:#3a4658;font-size:10px;font-weight:400;text-transform:none;letter-spacing:0" id="ai-tag">— · 정보형</span>
      </div>
      <div id="report-rows" style="padding:6px 0">
        <div class="report-row"><span class="k">상태</span><span class="v muted">티커를 입력하고 [분석 실행]을 누르세요.</span></div>
      </div>
      <div class="report-note">※ 매수/매도 추천이 아닌 현재 상태 요약입니다. 투자 책임은 본인에게 있습니다.</div>
    </div>
  </div>

  <div class="context-grid">
    <div class="box">
      <div class="box-head">
        <span>MY POSITION</span>
        <span class="total" id="position-tag">—</span>
      </div>
      <div id="position-rows">
        <div class="context-row"><span class="k">상태</span><span class="v muted">차트를 불러오는 중입니다.</span></div>
      </div>
    </div>

    <div class="box">
      <div class="box-head">
        <span>RECENT AI JUDGMENTS</span>
        <span class="total" id="judgment-tag">—</span>
      </div>
      <div id="judgment-rows">
        <div class="mini-list-row"><span class="muted">—</span><span class="muted">—</span><span class="muted">아직 데이터 없음</span></div>
      </div>
    </div>
  </div>

  <div class="err-banner" id="err" style="margin-top:16px"></div>

</div>

<script>
{COMMON_JS}

let _interval = "minute60";
const ivLabel = {{ "minute15":"15분", "minute60":"1시간", "day":"일봉" }};

function setAnalyzeTicker(ticker) {{
  document.getElementById("ticker-input").value = ticker;
  loadChart(ticker).catch(() => {{}});
  loadAnalyzeContext(ticker).catch(() => {{}});
}}

async function loadAnalyzeConfig() {{
  try {{
    const cfg = await (await fetch("/api/config")).json();
    document.getElementById("analysis-presets").innerHTML = (cfg.tickers || []).map(t =>
      `<button class="mini-btn" onclick="setAnalyzeTicker('${{t}}')">${{t}}</button>`
    ).join("");
  }} catch(e) {{}}
}}

document.querySelectorAll(".iv-btn").forEach(b => {{
  b.addEventListener("click", () => {{
    document.querySelectorAll(".iv-btn").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    _interval = b.dataset.iv;
    const ticker = document.getElementById("sel-ticker").textContent;
    if (ticker && ticker !== "—") loadChart(ticker).catch(() => {{}});
  }});
}});

async function loadState() {{
  try {{
    const s = await (await fetch("/api/state")).json();
    applyTerminalHeader(s);
    document.getElementById("ai-tag").textContent = s.provider + " · 정보형";
  }} catch(e) {{}}
}}

function renderAnalyzeContext(ctx) {{
  const h = ctx.holding || {{}};
  const hasHolding = !!ctx.holding;
  const positionRows = hasHolding ? [
    ["보유수량", NUM(h.balance || 0)],
    ["평균단가", KRW(h.avg_buy_price || 0) + " 원"],
    ["평가금액", KRW(h.current_value || 0) + " 원"],
    ["비중", Number(h.weight || 0).toFixed(1) + "%"],
    ["미실현", KRW(h.unrealized_pnl || 0, true) + " 원"],
    ["실현", KRW(h.realized_pnl || 0, true) + " 원"],
  ] : [
    ["보유상태", "미보유"],
    ["포트폴리오", "총 평가 " + KRW(ctx.portfolio_summary?.total_value || 0) + " 원"],
  ];
  document.getElementById("position-tag").textContent = hasHolding ? "보유중" : "watch only";
  document.getElementById("position-rows").innerHTML = positionRows.map(([k, v]) =>
    `<div class="context-row"><span class="k">${{k}}</span><span class="v">${{v}}</span></div>`
  ).join("");

  const decisions = ctx.decisions || [];
  document.getElementById("judgment-tag").textContent = decisions.length ? decisions.length + " rows" : "—";
  document.getElementById("judgment-rows").innerHTML = decisions.map(d => {{
    const time = (d.ts || "").replace("T", " ").slice(5, 16);
    const action = d.action || "HOLD";
    return `<div class="mini-list-row">
      <span class="muted">${{time || "—"}}</span>
      <span><span class="action-chip act-${{action}}">${{action}}</span></span>
      <span class="mini-reason" title="${{escapeHtml(cleanReason(d.reasoning, 500))}}">${{escapeHtml(cleanReason(d.reasoning, 120))}}</span>
    </div>`;
  }}).join("") || `<div class="mini-list-row"><span class="muted">—</span><span class="muted">—</span><span class="muted">최근 판단 없음</span></div>`;
}}

async function loadAnalyzeContext(ticker) {{
  const ctx = await (await fetch(`/api/analyze_context?ticker=${{ticker}}`)).json();
  renderAnalyzeContext(ctx);
}}

async function loadChart(ticker) {{
  const j = await (await fetch(`/api/candles?ticker=${{ticker}}&interval=${{_interval}}&count=120`)).json();
  if (j.error) throw new Error(j.error);
  const stats = renderChart("priceSvg", "rsiSvg", j);
  document.getElementById("chart-title").textContent = ticker + " · " + ivLabel[_interval];
  document.getElementById("chart-rsi").textContent = stats.lastRsi != null ? stats.lastRsi.toFixed(1) : "—";

  // 스냅카드
  const rsi = stats.lastRsi;
  const rsiState = rsi >= 70 ? "과매수" : rsi <= 30 ? "과매도" : "중립";
  const rsiCol = rsi >= 70 ? "down" : rsi <= 30 ? "up" : "";

  document.getElementById("s-price").textContent = KRW(stats.lastClose) + " 원";
  document.getElementById("s-price-sub").textContent = ticker;

  const rsiEl = document.getElementById("s-rsi");
  rsiEl.textContent = rsi != null ? rsi.toFixed(1) : "—";
  rsiEl.className = "val " + rsiCol;
  document.getElementById("s-rsi-sub").textContent = rsiState;

  document.getElementById("s-ma").textContent = KRW(stats.lastMa5);
  document.getElementById("s-ma-sub").textContent = "MA20 " + KRW(stats.lastMa20);

  const chgEl = document.getElementById("s-chg");
  chgEl.textContent = PCT(stats.changePct);
  chgEl.className = "val " + colorOf(stats.changePct);

  document.getElementById("sel-ticker").textContent = ticker;
  await loadAnalyzeContext(ticker);
}}

async function loadReport(ticker) {{
  const r = await fetch(`/api/analyze?ticker=${{ticker}}&interval=${{_interval}}&count=120`);
  const j = await r.json();
  if (j.error) throw new Error(j.error);
  const tag = document.getElementById("ai-tag");
  if (tag) {{
    const base = tag.textContent.replace(" · cached", "");
    tag.textContent = base + (j.cached ? " · cached" : "");
  }}
  const rep = j.report;
  const fields = [
    ["요약", rep.summary],
    ["추세", rep.trend],
    ["모멘텀", rep.momentum],
    ["지지/저항", rep.support_resistance],
    ["리스크", rep.risks],
    ["관찰 신호", rep.watch],
  ];
  document.getElementById("report-rows").innerHTML = fields.map(([k,v]) => `
    <div class="report-row"><span class="k">${{k}}</span><span class="v">${{v || "—"}}</span></div>
  `).join("");
}}

async function run() {{
  const err = document.getElementById("err");
  err.style.display = "none";
  const ticker = (document.getElementById("ticker-input").value || "").trim().toUpperCase();
  if (!ticker.startsWith("KRW-")) {{
    err.style.display = "block";
    err.textContent = "티커는 KRW-XXX 형식이어야 합니다";
    return;
  }}
  document.getElementById("report-rows").innerHTML =
    `<div class="report-row"><span class="k">상태</span><span class="v muted">분석 중...</span></div>`;
  try {{
    await loadChart(ticker);
    await loadReport(ticker);
  }} catch(e) {{
    err.style.display = "block";
    err.textContent = "❌ " + e.message;
  }}
}}

async function bootAnalyzePage() {{
  try {{
    await loadChart((document.getElementById("ticker-input").value || "KRW-BTC").trim().toUpperCase());
  }} catch(e) {{}}
}}

loadState();
loadTickerTape();
loadAnalyzeConfig();
bootAnalyzePage();
setInterval(loadState, 3000);
setInterval(loadTickerTape, 15000);
</script>
</body></html>"""


def main() -> None:
    start_background_trading()

    url = f"http://localhost:{config.WEB_PORT}"
    print(f"\n✅ stockagent Terminal: {url}")
    print(f"🔎 종목 분석: {url}/analyze\n")
    if config.AUTO_OPEN_BROWSER:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
