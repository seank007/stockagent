"""Realtime market-data streams bridged to browser Server-Sent Events."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
import json
import ssl
import time
import uuid

from websockets.sync.client import connect

from brokers.kis import KISBroker

try:
    import certifi
except Exception:  # noqa: BLE001
    certifi = None


UPBIT_WEBSOCKET_URL = "wss://api.upbit.com/websocket/v1"
KIS_TRADE_TR_ID = "H0UNCNT0"
KIS_TRADE_FIELD_COUNT = 46
KST = timezone(timedelta(hours=9))
UPBIT_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _float(value: object, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _millis_iso(value: object) -> str | None:
    millis = _float(value)
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat(timespec="milliseconds")


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _sse_prelude() -> str:
    # The padding prevents reverse proxies from waiting for a larger first chunk.
    return "retry: 1500\n: " + (" " * 2048) + "\n\n"


def _status(market: str, state: str, **extra: object) -> str:
    return _sse("status", {
        "market": market,
        "state": state,
        "received_at": _now_iso(),
        **extra,
    })


def normalize_upbit_message(message: dict) -> tuple[str, dict] | None:
    """Reduce an Upbit WebSocket payload to the fields used by the dashboard."""
    message_type = message.get("type")
    ticker = str(message.get("code") or "").upper()
    received_at = _now_iso()

    if message_type == "ticker":
        return "ticker", {
            "ticker": ticker,
            "price": _float(message.get("trade_price")),
            "change": message.get("change"),
            "change_price": _float(message.get("signed_change_price")),
            "change_rate": _float(message.get("signed_change_rate")),
            "trade_volume": _float(message.get("trade_volume")),
            "acc_trade_volume_24h": _float(message.get("acc_trade_volume_24h")),
            "timestamp": message.get("timestamp"),
            "traded_at": _millis_iso(message.get("trade_timestamp")),
            "received_at": received_at,
            "source": "UPBIT_WS",
        }

    if message_type == "orderbook":
        units = []
        for unit in message.get("orderbook_units") or []:
            units.append({
                "ask_price": _float(unit.get("ask_price")),
                "bid_price": _float(unit.get("bid_price")),
                "ask_size": _float(unit.get("ask_size")),
                "bid_size": _float(unit.get("bid_size")),
            })
        return "orderbook", {
            "ticker": ticker,
            "timestamp": message.get("timestamp"),
            "updated_at": _millis_iso(message.get("timestamp")),
            "received_at": received_at,
            "total_ask_size": _float(message.get("total_ask_size"), 0.0),
            "total_bid_size": _float(message.get("total_bid_size"), 0.0),
            "units": units,
            "source": "UPBIT_WS",
        }

    return None


def coin_sse_stream(ticker: str) -> Iterator[str]:
    """Yield snapshot and realtime Upbit ticker/orderbook events as SSE."""
    ticker = ticker.upper()
    backoff = 1.0
    yield _sse_prelude()

    while True:
        try:
            yield _status("coin", "connecting", ticker=ticker, source="UPBIT_WS")
            with connect(
                UPBIT_WEBSOCKET_URL,
                ssl=UPBIT_SSL_CONTEXT,
                open_timeout=10,
                close_timeout=2,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            ) as websocket:
                request = [
                    {"ticket": f"stockagent-{uuid.uuid4().hex[:12]}"},
                    {"type": "ticker", "codes": [ticker]},
                    {"type": "orderbook", "codes": [ticker]},
                    {"format": "DEFAULT"},
                ]
                websocket.send(json.dumps(request, separators=(",", ":")))
                yield _status("coin", "connected", ticker=ticker, source="UPBIT_WS")
                backoff = 1.0

                while True:
                    try:
                        raw = websocket.recv(timeout=25)
                    except TimeoutError:
                        yield _sse("heartbeat", {
                            "market": "coin",
                            "ticker": ticker,
                            "received_at": _now_iso(),
                        })
                        continue
                    if not raw:
                        raise ConnectionError("Upbit WebSocket closed")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    normalized = normalize_upbit_message(json.loads(raw))
                    if normalized:
                        yield _sse(*normalized)
        except GeneratorExit:
            return
        except Exception as exc:  # noqa: BLE001 - stream reconnect boundary
            yield _status(
                "coin",
                "reconnecting",
                ticker=ticker,
                source="UPBIT_WS",
                retry_in=backoff,
                error=str(exc)[:240],
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)


def _signed_kis_number(sign: str, value: object) -> float:
    number = abs(_float(value, 0.0) or 0.0)
    if sign in {"4", "5"}:
        return -number
    if sign == "3":
        return 0.0
    return number


def _kis_traded_at(business_date: str, trade_time: str) -> str | None:
    value = f"{business_date}{trade_time}".strip()
    try:
        parsed = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")


def parse_kis_trade_message(raw: str) -> list[dict]:
    """Parse one KIS unified-market realtime frame, including batched trades."""
    if not raw.startswith(("0|", "1|")):
        return []
    parts = raw.split("|", 3)
    if len(parts) != 4 or parts[1] != KIS_TRADE_TR_ID:
        return []
    try:
        count = int(parts[2])
    except ValueError:
        return []

    values = parts[3].split("^")
    trades = []
    for index in range(count):
        start = index * KIS_TRADE_FIELD_COUNT
        row = values[start:start + KIS_TRADE_FIELD_COUNT]
        if len(row) < KIS_TRADE_FIELD_COUNT:
            break
        sign = row[3]
        trades.append({
            "code": row[0],
            "trade_time": row[1],
            "price": _float(row[2], 0.0),
            "change": _signed_kis_number(sign, row[4]),
            "change_pct": _signed_kis_number(sign, row[5]),
            "open": _float(row[7], 0.0),
            "high": _float(row[8], 0.0),
            "low": _float(row[9], 0.0),
            "ask_price": _float(row[10], 0.0),
            "bid_price": _float(row[11], 0.0),
            "trade_volume": _float(row[12], 0.0),
            "acc_volume": _float(row[13], 0.0),
            "business_date": row[33],
            "session_code": row[43],
            "traded_at": _kis_traded_at(row[33], row[1]),
            "received_at": _now_iso(),
            "source": "KIS_WS",
        })
    return trades


def _kis_subscription(approval_key: str, code: str) -> str:
    return json.dumps({
        "header": {
            "approval_key": approval_key,
            "custtype": "P",
            "tr_type": "1",
            "content-type": "utf-8",
        },
        "body": {
            "input": {
                "tr_id": KIS_TRADE_TR_ID,
                "tr_key": code,
            },
        },
    }, separators=(",", ":"))


def stock_sse_stream(codes: list[str]) -> Iterator[str]:
    """Yield realtime KIS domestic-stock trades as SSE."""
    codes = list(dict.fromkeys(code for code in codes if code))[:40]
    broker = KISBroker()
    backoff = 1.0
    yield _sse_prelude()

    if not broker.is_realtime_configured():
        yield _status(
            "stock",
            "unavailable",
            codes=codes,
            source="KIS_WS",
            reason="missing_kis_credentials",
            required=["KIS_APP_KEY", "KIS_APP_SECRET"],
        )
        while True:
            try:
                time.sleep(15)
                yield _sse("heartbeat", {
                    "market": "stock",
                    "state": "unavailable",
                    "received_at": _now_iso(),
                })
            except GeneratorExit:
                return

    while True:
        try:
            yield _status("stock", "connecting", codes=codes, source="KIS_WS")
            approval_key = broker.websocket_approval_key()
            with connect(
                broker.websocket_url,
                open_timeout=10,
                close_timeout=2,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            ) as websocket:
                for code in codes:
                    websocket.send(_kis_subscription(approval_key, code))
                    time.sleep(0.1)
                yield _status(
                    "stock",
                    "connected",
                    codes=codes,
                    source="KIS_WS",
                    paper=broker.paper,
                )
                backoff = 1.0

                while True:
                    try:
                        raw = websocket.recv(timeout=25)
                    except TimeoutError:
                        yield _sse("heartbeat", {
                            "market": "stock",
                            "codes": codes,
                            "received_at": _now_iso(),
                        })
                        continue
                    if not raw:
                        raise ConnectionError("KIS WebSocket closed")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")

                    if raw.startswith(("0|", "1|")):
                        for trade in parse_kis_trade_message(raw):
                            yield _sse("stock", trade)
                        continue

                    message = json.loads(raw)
                    header = message.get("header") or {}
                    if header.get("tr_id") == "PINGPONG":
                        websocket.pong(raw)
                        continue
                    body = message.get("body") or {}
                    if body and str(body.get("rt_cd")) != "0":
                        raise RuntimeError(body.get("msg1") or "KIS subscription rejected")
        except GeneratorExit:
            return
        except Exception as exc:  # noqa: BLE001 - stream reconnect boundary
            yield _status(
                "stock",
                "reconnecting",
                codes=codes,
                source="KIS_WS",
                retry_in=backoff,
                error=str(exc)[:240],
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
