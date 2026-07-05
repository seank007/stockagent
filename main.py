"""stockagent 메인 루프.

수집 → AI 판단 → 리스크 검사 → 주문 실행 → DB 기록을 주기적으로 반복한다.
실행:
  python main.py        # 터미널 로그만
  python web.py         # 웹 대시보드 + 루프 (브라우저에서 보기)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time
from datetime import datetime, timedelta

import config
import db
from agent.decision import DecisionAgent
from brokers.upbit import UpbitBroker
from risk import RiskManager
from state import store


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _record_trade_safe(ticker: str, side: str, price: float, volume: float,
                       krw_amount: float, raw: dict) -> dict:
    try:
        return db.record_trade(
            ticker=ticker,
            side=side,
            price=price,
            volume=volume,
            krw_amount=krw_amount,
            dry_run=config.DRY_RUN,
            raw_result=json.dumps(raw, ensure_ascii=False, default=str),
        )
    except Exception as e:  # noqa: BLE001
        log(f"  ! DB 기록 실패: {e}")
        return {"realized_pnl": 0.0, "fee": 0.0}


def _prepare_ticker_decision(
    ticker: str,
    broker: UpbitBroker,
    agent: DecisionAgent,
    krw: float,
    balances: list[dict] | None = None,
) -> dict:
    snapshot = broker.market_snapshot(ticker)
    if balances is None:
        coin_balance = broker.get_coin_balance(ticker)
        avg_price = broker.get_avg_buy_price(ticker)
    else:
        coin_balance, avg_price = broker.position_from_balances(ticker, balances)
    position = {
        "coin_balance": coin_balance,
        "avg_buy_price": avg_price,
        "krw_balance": krw,
    }
    decision = agent.decide(snapshot, position)
    return {
        "ticker": ticker,
        "snapshot": snapshot,
        "coin_balance": coin_balance,
        "avg_buy_price": avg_price,
        "decision": decision,
    }


def _prepare_all_tickers(
    broker: UpbitBroker,
    agent: DecisionAgent,
    krw: float,
    balances: list[dict] | None = None,
) -> dict[str, dict]:
    if not config.TICKERS:
        return {}

    workers = min(
        max(1, int(getattr(config, "MAX_DECISION_WORKERS", 1))),
        len(config.TICKERS),
    )
    if workers == 1:
        prepared: dict[str, dict] = {}
        for ticker in config.TICKERS:
            try:
                prepared[ticker] = _prepare_ticker_decision(ticker, broker, agent, krw, balances)
            except Exception as e:  # noqa: BLE001
                log(f"{ticker} 판단 준비 실패: {e}")
        return prepared

    prepared = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ticker") as executor:
        futures = {
            executor.submit(_prepare_ticker_decision, ticker, broker, agent, krw, balances): ticker
            for ticker in config.TICKERS
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                prepared[ticker] = future.result()
            except Exception as e:  # noqa: BLE001
                log(f"{ticker} 판단 준비 실패: {e}")
    return prepared


def run_once(broker: UpbitBroker, agent: DecisionAgent, risk: RiskManager) -> None:
    balances: list[dict] | None = None
    try:
        balances = broker.get_balances()
        krw = broker.krw_from_balances(balances)
    except Exception as e:  # noqa: BLE001
        log(f"계좌 잔고 일괄 조회 실패: {e}")
        balances = None
        krw = broker.get_krw_balance()
    store.set_krw(krw)

    try:
        portfolio = broker.get_portfolio(balances)
        store.update_portfolio(portfolio)
    except Exception as e:  # noqa: BLE001
        log(f"포트폴리오 조회 실패: {e}")

    started = time.perf_counter()
    prepared: dict[str, dict] = {}
    try:
        prepared = _prepare_all_tickers(broker, agent, krw, balances)
        if len(prepared) == len(config.TICKERS):
            store.set_error(None)
        else:
            store.set_error(f"{len(config.TICKERS) - len(prepared)}개 종목 판단 준비 실패")
    except Exception as e:  # noqa: BLE001
        log(f"종목 판단 준비 실패: {e}")
        store.set_error(f"종목 판단 준비 실패: {e}")

    elapsed = time.perf_counter() - started
    log(f"판단 준비 완료 {len(prepared)}/{len(config.TICKERS)}종목 | {elapsed:.2f}s")

    available_krw = krw
    for ticker in config.TICKERS:
        item = prepared.get(ticker)
        if item is None:
            continue

        snapshot = item["snapshot"]
        coin_balance = float(item["coin_balance"])
        decision = item["decision"]
        log(
            f"{ticker} price={snapshot['price']:,} rsi={snapshot['rsi14']} "
            f"trend={snapshot['trend']} → {decision.action} "
            f"(conf={decision.confidence:.2f}) :: {decision.reasoning}"
        )

        avg_buy_price = float(item["avg_buy_price"])
        order = risk.evaluate(decision, snapshot, available_krw, coin_balance, avg_buy_price)
        price_now = float(snapshot["price"])

        if order.side == "buy":
            result = broker.buy(ticker, order.krw_amount)
            available_krw = max(0.0, available_krw - order.krw_amount)
            volume = order.krw_amount / price_now if price_now > 0 else 0.0
            trade_info = _record_trade_safe(
                ticker, "buy", price_now, volume, order.krw_amount, result
            )
            log(f"  ▶ 매수 {order.krw_amount:,}원 (≈{volume:.6f}) | {order.reason} | {result}")
        elif order.side == "sell":
            result = broker.sell(ticker, order.volume)
            krw_value = order.volume * price_now
            available_krw += krw_value
            trade_info = _record_trade_safe(
                ticker, "sell", price_now, order.volume, krw_value, result
            )
            log(
                f"  ▶ 매도 {order.volume} (≈{krw_value:,.0f}원) | "
                f"실현손익 {trade_info['realized_pnl']:+,.0f}원 | {order.reason}"
            )
        else:
            log(f"  · 주문 없음 | {order.reason}")

        try:
            db.save_decision(ticker, snapshot, decision, order)
        except Exception as e:  # noqa: BLE001
            log(f"  ! 판단 기록 실패: {e}")

        store.update_ticker(ticker, snapshot, decision, order)

    # 일일 손익 요약을 store에 반영
    try:
        store.set_pnl(db.get_today_realized_pnl(), db.total_realized_pnl())
    except Exception:  # noqa: BLE001
        pass


def trading_loop() -> None:
    """매매 로직 1회 실행."""
    config.validate()
    mode = "모의매매(DRY_RUN)" if config.DRY_RUN else "⚠️ 실거래"
    log(f"stockagent 수동/1회 실행 | 모드: {mode} | AI: {config.AI_PROVIDER} | 종목: {config.TICKERS}")

    try:
        store.hydrate_from_db()
    except Exception as e:  # noqa: BLE001
        log(f"DB 복원 실패(무시): {e}")

    broker = UpbitBroker()
    agent = DecisionAgent()
    risk = RiskManager()

    store.set_loop_running(True)
    try:
        store.mark_cycle_start()
        run_once(broker, agent, risk)
        store.mark_cycle_end(None)
    except Exception as e:  # noqa: BLE001
        log(f"실행 오류: {e}")
        store.set_error(str(e))
        store.mark_cycle_end(None)
    finally:
        store.set_loop_running(False)


if __name__ == "__main__":
    trading_loop()
