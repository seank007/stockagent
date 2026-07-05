"""hermes agent용 매매 CLI.

hermes agent(자율 트레이더)가 터미널에서 호출하는 손발이다. 계좌·시세 조회,
시장가 매수/매도 실행, 판단 근거 기록을 제공하고 모든 결과를 JSON으로 출력한다.
stockagent 대시보드·DB를 그대로 공유하므로 웹 화면에도 전부 표시된다.

사용법:
  python scripts/trade_cli.py status                       # 계좌 + 시세 + 최근 기록
  python scripts/trade_cli.py buy KRW-BTC 10000            # 1만원 시장가 매수 (max = 전액)
  python scripts/trade_cli.py sell KRW-SOL 50              # 보유분 50% 시장가 매도
  python scripts/trade_cli.py log KRW-BTC BUY 0.8 "근거…"  # 판단 근거를 대시보드에 기록
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db  # noqa: E402
from brokers.upbit import UpbitBroker  # noqa: E402
from risk import Order  # noqa: E402


def _out(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _fail(msg: str) -> None:
    _out({"ok": False, "error": msg})
    sys.exit(1)


def cmd_status(broker: UpbitBroker) -> None:
    balances = broker.get_balances()
    krw = broker.krw_from_balances(balances)
    markets = {}
    for ticker in config.TICKERS:
        snap = broker.market_snapshot(ticker)
        coin, avg = broker.position_from_balances(ticker, balances)
        markets[ticker] = {
            "price": snap.get("price"),
            "rsi14": snap.get("rsi14"),
            "trend": snap.get("trend"),
            "ma5": snap.get("ma5"),
            "ma20": snap.get("ma20"),
            "period_change_pct": snap.get("period_change_pct"),
            "recent_closes": (snap.get("closes") or [])[-10:],
            "my_balance": coin,
            "my_avg_buy_price": avg,
            "my_value_krw": round(coin * float(snap.get("price") or 0)),
        }
    _out({
        "ok": True,
        "krw_balance": krw,
        "markets": markets,
        "exchange_rules": {"min_order_krw": config.UPBIT_MIN_ORDER_KRW, "fee_rate": 0.0005},
        "recent_trades": db.recent_trades(limit=5),
        "recent_decisions": [
            {k: d.get(k) for k in ("ts", "ticker", "action", "reasoning")}
            for d in db.recent_decisions(limit=6)
        ],
        "today_realized_pnl": db.get_today_realized_pnl(),
    })


def cmd_buy(broker: UpbitBroker, ticker: str, amount_arg: str) -> None:
    krw = broker.get_krw_balance()
    amount = krw if amount_arg.lower() == "max" else float(amount_arg)
    amount = min(amount, krw)
    if amount < config.UPBIT_MIN_ORDER_KRW:
        _fail(f"매수 금액 {amount:,.0f}원 < 업비트 최소 5,000원 (원화 잔고 {krw:,.0f}원)")
    snap = broker.market_snapshot(ticker)
    price = float(snap.get("price") or 0)
    result = broker.buy(ticker, amount)
    if not result or (isinstance(result, dict) and result.get("error")):
        _fail(f"매수 주문이 거래소에서 거절됨: {result!r} "
              f"(잔고 {krw:,.0f}원, 주문 {amount:,.0f}원 — 금액을 줄여 재시도하라)")
    volume = amount / price if price > 0 else 0.0
    db.record_trade(
        ticker=ticker, side="buy", price=price, volume=volume, krw_amount=amount,
        dry_run=config.DRY_RUN, raw_result=json.dumps(result, ensure_ascii=False, default=str),
    )
    _out({"ok": True, "side": "buy", "ticker": ticker, "krw_amount": round(amount),
          "approx_volume": volume, "price": price, "exchange_result": result})


def cmd_sell(broker: UpbitBroker, ticker: str, pct_arg: str) -> None:
    pct = max(1.0, min(float(pct_arg), 100.0))
    coin = broker.get_coin_balance(ticker)
    if coin <= 0:
        _fail(f"{ticker} 보유 수량 없음")
    snap = broker.market_snapshot(ticker)
    price = float(snap.get("price") or 0)
    volume = coin * pct / 100.0
    value = volume * price
    if value < config.UPBIT_MIN_ORDER_KRW:
        _fail(f"매도 금액 {value:,.0f}원 < 업비트 최소 5,000원 "
              f"(보유 전량 {coin}개 ≈ {coin * price:,.0f}원, pct를 키워라)")
    result = broker.sell(ticker, volume)
    if not result or (isinstance(result, dict) and result.get("error")):
        _fail(f"매도 주문이 거래소에서 거절됨: {result!r}")
    info = db.record_trade(
        ticker=ticker, side="sell", price=price, volume=volume, krw_amount=value,
        dry_run=config.DRY_RUN, raw_result=json.dumps(result, ensure_ascii=False, default=str),
    )
    _out({"ok": True, "side": "sell", "ticker": ticker, "volume": volume,
          "approx_krw": round(value), "realized_pnl": info.get("realized_pnl"),
          "exchange_result": result})


def cmd_log(broker: UpbitBroker, ticker: str, action: str, confidence: str, reasoning: str) -> None:
    snap = broker.market_snapshot(ticker)

    class _D:
        pass

    d = _D()
    d.action = action.upper()
    d.confidence = float(confidence)
    d.reasoning = reasoning
    order = Order("none", ticker, reason="hermes agent 판단")
    db.save_decision(ticker, snap, d, order)
    _out({"ok": True, "logged": {"ticker": ticker, "action": d.action, "reasoning": reasoning}})


def main() -> None:
    parser = argparse.ArgumentParser(description="hermes agent 매매 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_buy = sub.add_parser("buy")
    p_buy.add_argument("ticker")
    p_buy.add_argument("krw_amount", help="원화 금액 또는 'max'")
    p_sell = sub.add_parser("sell")
    p_sell.add_argument("ticker")
    p_sell.add_argument("pct", help="보유 수량 대비 퍼센트 (1~100)")
    p_log = sub.add_parser("log")
    p_log.add_argument("ticker")
    p_log.add_argument("action", choices=["BUY", "SELL", "HOLD", "buy", "sell", "hold"])
    p_log.add_argument("confidence")
    p_log.add_argument("reasoning")
    args = parser.parse_args()

    broker = UpbitBroker()
    try:
        if args.cmd == "status":
            cmd_status(broker)
        elif args.cmd == "buy":
            cmd_buy(broker, args.ticker.upper(), args.krw_amount)
        elif args.cmd == "sell":
            cmd_sell(broker, args.ticker.upper(), args.pct)
        elif args.cmd == "log":
            cmd_log(broker, args.ticker.upper(), args.action, args.confidence, args.reasoning)
    except Exception as e:  # noqa: BLE001
        _fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
