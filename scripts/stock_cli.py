"""hermes agent용 국내주식 매매 CLI (trade_cli.py의 주식 버전).

KIS 키가 .env에 있으면 한국투자증권(실전/모의)으로, 없으면 페이퍼(가상 예수금
1천만원 + 네이버 실시세)로 동작한다. 모든 결과는 JSON으로 출력한다.

사용법:
  python scripts/stock_cli.py market                    # 장 열림 여부
  python scripts/stock_cli.py status                    # 계좌 + 기본 종목 상태
  python scripts/stock_cli.py status 005930 000660      # 특정 종목 상태
  python scripts/stock_cli.py buy 005930 500000         # 약 50만원어치 시장가 매수
  python scripts/stock_cli.py buy 005930 3s             # 3주 매수 (숫자+s = 주 단위)
  python scripts/stock_cli.py sell 005930 50            # 보유분 50% 시장가 매도
  python scripts/stock_cli.py log 005930 HOLD 0.7 "근거…"  # 판단 기록
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stock_db  # noqa: E402
from brokers.kis import get_stock_broker, market_status, naver_closes  # noqa: E402

DEFAULT_CODES = ["005930", "000660", "035420", "005380", "035720"]


def out(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    gains = losses = 0.0
    for i in range(len(closes) - 14, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def _indicators(code: str) -> dict:
    try:
        closes = naver_closes(code, 40)
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        trend = None
        if ma5 and ma20:
            trend = "up" if ma5 > ma20 else "down"
        chg5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
        return {"rsi14": _rsi14(closes), "ma5": ma5, "ma20": ma20,
                "trend": trend, "change_5d_pct": chg5}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"지표 계산 실패: {exc}"}


def cmd_market(_args) -> None:
    out(market_status())


def cmd_status(args) -> None:
    broker = get_stock_broker()
    bal = broker.balance()
    codes = args.codes or list(dict.fromkeys(
        [h["code"] for h in bal["holdings"]] + DEFAULT_CODES))[:8]
    items = []
    for code in codes:
        try:
            q = broker.quote(code)
        except Exception as exc:  # noqa: BLE001
            items.append({"code": code, "error": str(exc)})
            continue
        held = next((h for h in bal["holdings"] if h["code"] == code), None)
        items.append({**q, **_indicators(code),
                      "holding": held or {"qty": 0}})
    out({
        "market": market_status(),
        "mode": bal["source"],
        "cash_krw": bal["cash"],
        "total_eval_krw": bal["total_eval"],
        "holdings": bal["holdings"],
        "watch": items,
        "recent_trades": stock_db.recent_trades(limit=10),
        "recent_decisions": stock_db.recent_decisions(limit=10),
        "rules": {
            "buy": "buy <code> <원화금액 | N s(주)>  — 시장가",
            "sell": "sell <code> <보유비율%>  — 시장가",
            "note": "장중(평일 09:00~15:30)에만 주문. 공휴일은 주문 실패로 확인됨.",
        },
    })


def cmd_buy(args) -> None:
    ms = market_status()
    broker = get_stock_broker()
    q = broker.quote(args.code)
    if str(args.amount).lower().endswith("s"):
        qty = int(str(args.amount)[:-1])
    else:
        krw = float(args.amount)
        qty = int(krw // q["price"]) if q["price"] > 0 else 0
    if qty < 1:
        out({"error": f"주문 수량 0주 (현재가 {q['price']:,.0f}원, 금액을 키우세요)"})
        sys.exit(1)
    if not ms["open"] and not getattr(broker, "is_paper_broker", False):
        out({"error": f"장외 시간 주문 불가: {ms['note']}"})
        sys.exit(1)
    try:
        result = broker.market_buy(args.code, qty)
        out({"ok": True, "order": result, "market": ms})
    except Exception as exc:  # noqa: BLE001
        out({"error": str(exc)})
        sys.exit(1)


def cmd_sell(args) -> None:
    ms = market_status()
    broker = get_stock_broker()
    bal = broker.balance()
    held = next((h for h in bal["holdings"] if h["code"] == args.code), None)
    if not held or held["qty"] < 1:
        out({"error": f"{args.code} 보유 없음"})
        sys.exit(1)
    pct = max(1.0, min(100.0, float(args.percent)))
    qty = max(1, int(held["qty"] * pct / 100))
    if not ms["open"] and not getattr(broker, "is_paper_broker", False):
        out({"error": f"장외 시간 주문 불가: {ms['note']}"})
        sys.exit(1)
    try:
        result = broker.market_sell(args.code, qty)
        out({"ok": True, "order": result, "market": ms})
    except Exception as exc:  # noqa: BLE001
        out({"error": str(exc)})
        sys.exit(1)


def cmd_log(args) -> None:
    broker = get_stock_broker()
    try:
        q = broker.quote(args.code)
    except Exception:  # noqa: BLE001
        q = {"name": args.code, "price": None, "change_pct": None}
    ind = _indicators(args.code)
    stock_db.save_decision(
        code=args.code, name=q.get("name") or args.code, price=q.get("price"),
        rsi=ind.get("rsi14"), trend=ind.get("trend"),
        change_pct=q.get("change_pct"),
        action=args.action.upper(), confidence=float(args.confidence),
        reasoning=args.reason,
        order_side="none", order_reason="hermes agent 판단",
    )
    out({"ok": True, "logged": {"code": args.code, "action": args.action.upper()}})


def main() -> None:
    parser = argparse.ArgumentParser(description="stockagent 주식 매매 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("market").set_defaults(func=cmd_market)

    p = sub.add_parser("status")
    p.add_argument("codes", nargs="*", help="종목코드 6자리 (생략 시 보유+기본 종목)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("buy")
    p.add_argument("code")
    p.add_argument("amount", help="원화 금액 또는 'N s'(주 수량, 예: 3s)")
    p.set_defaults(func=cmd_buy)

    p = sub.add_parser("sell")
    p.add_argument("code")
    p.add_argument("percent", help="보유 수량 대비 %%")
    p.set_defaults(func=cmd_sell)

    p = sub.add_parser("log")
    p.add_argument("code")
    p.add_argument("action", choices=["BUY", "SELL", "HOLD", "buy", "sell", "hold"])
    p.add_argument("confidence", type=float)
    p.add_argument("reason")
    p.set_defaults(func=cmd_log)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
