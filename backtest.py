"""stockagent 백테스트.

과거 캔들로 매매 시뮬레이션 → 수익률·MDD·승률·거래수 리포트.

사용:
  python backtest.py KRW-BTC --interval minute60 --count 500
  python backtest.py KRW-BTC --mode ai --interval minute60 --count 200
  python backtest.py KRW-BTC KRW-SOL --interval day --count 300

기본 모드는 'rule' (RSI+MA 룰 기반, AI 비용 없음).
--mode ai 를 주면 실제 DecisionAgent 를 매 캔들 호출 (느리고 비용 발생).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Callable

import pyupbit

import config


# ------------------- 지표 -------------------
def _rsi_series(close, period: int = 14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


# ------------------- 룰 기반 전략 -------------------
def rule_signal(row: dict) -> tuple[str, float]:
    """간단한 RSI+MA 추세 룰. (action, confidence) 반환."""
    rsi = row["rsi14"]
    ma5, ma20 = row["ma5"], row["ma20"]
    if rsi < 30 and ma5 > ma20:
        return "BUY", 0.75
    if rsi < 35 and ma5 > ma20 * 0.995:
        return "BUY", 0.62
    if rsi > 70:
        return "SELL", 0.75
    if rsi > 65 and ma5 < ma20:
        return "SELL", 0.62
    return "HOLD", 0.5


# ------------------- AI 전략(옵션) -------------------
def ai_signal_factory() -> Callable[[dict, dict], tuple[str, float]]:
    """AI provider를 한 번 만들어 매 캔들 재사용."""
    from agent.decision import DecisionAgent

    agent = DecisionAgent()

    def _signal(snapshot: dict, position: dict) -> tuple[str, float]:
        d = agent.decide(snapshot, position)
        return d.action, float(d.confidence)

    return _signal


# ------------------- 시뮬레이터 -------------------
@dataclass
class Trade:
    side: str
    ts: str
    price: float
    volume: float
    krw: float
    realized_pnl: float = 0.0


@dataclass
class Result:
    ticker: str
    start_krw: float
    end_value: float
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    final_position_volume: float = 0.0
    final_avg_price: float = 0.0
    final_price: float = 0.0

    @property
    def total_return_pct(self) -> float:
        return (self.end_value / self.start_krw - 1) * 100 if self.start_krw else 0

    @property
    def realized_pnl(self) -> float:
        return sum(t.realized_pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        closes = [t for t in self.trades if t.side == "sell" and t.realized_pnl != 0]
        if not closes:
            return 0.0
        wins = sum(1 for t in closes if t.realized_pnl > 0)
        return wins / len(closes) * 100

    @property
    def mdd_pct(self) -> float:
        peak = -1e18
        mdd = 0.0
        for v in self.equity_curve:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, (v / peak - 1) * 100)
        return mdd  # 음수 (예: -12.3)

    @property
    def buy_count(self) -> int:
        return sum(1 for t in self.trades if t.side == "buy")

    @property
    def sell_count(self) -> int:
        return sum(1 for t in self.trades if t.side == "sell")


def simulate(
    ticker: str,
    df,
    *,
    start_krw: float = 1_000_000,
    fee_rate: float = 0.0005,
    min_confidence: float = 0.6,
    max_order_krw: float = 100_000,
    signal_fn: Callable = None,
    mode: str = "rule",
) -> Result:
    """df: pyupbit ohlcv DataFrame. 캔들 마감 시점 close 기준 체결로 단순화."""
    close = df["close"]
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    rsi = _rsi_series(close, 14)

    cash = start_krw
    volume = 0.0
    avg_price = 0.0
    result = Result(ticker=ticker, start_krw=start_krw, end_value=start_krw)

    for i in range(len(df)):
        price = float(close.iloc[i])
        if i < 20:  # 워밍업
            result.equity_curve.append(cash + volume * price)
            continue

        snapshot = {
            "ticker": ticker,
            "price": price,
            "ma5": float(ma5.iloc[i]),
            "ma20": float(ma20.iloc[i]),
            "rsi14": float(rsi.iloc[i]) if rsi.iloc[i] == rsi.iloc[i] else 50.0,
            "trend": "up" if ma5.iloc[i] > ma20.iloc[i] else "down",
            "period_change_pct": 0.0,
            "candle_interval": "",
            "recent_candles": [],
        }
        position = {"coin_balance": volume, "avg_buy_price": avg_price, "krw_balance": cash}

        if mode == "ai" and signal_fn is not None:
            action, conf = signal_fn(snapshot, position)
        else:
            action, conf = rule_signal(snapshot)

        ts = str(df.index[i])
        if action == "BUY" and conf >= min_confidence and cash > 5_000:
            order_krw = min(max_order_krw * conf, max_order_krw, cash)
            if order_krw >= 5_000:
                fee = order_krw * fee_rate
                got = (order_krw - fee) / price
                new_vol = volume + got
                avg_price = ((volume * avg_price) + (got * price)) / new_vol if new_vol else 0.0
                volume = new_vol
                cash -= order_krw
                result.trades.append(Trade("buy", ts, price, got, order_krw))
        elif action == "SELL" and conf >= min_confidence and volume > 0:
            gross = volume * price
            fee = gross * fee_rate
            realized = (price - avg_price) * volume - fee if avg_price > 0 else 0.0
            cash += gross - fee
            result.trades.append(Trade("sell", ts, price, volume, gross, realized))
            volume = 0.0
            avg_price = 0.0

        result.equity_curve.append(cash + volume * price)

    final_price = float(close.iloc[-1])
    result.end_value = cash + volume * final_price
    result.final_position_volume = volume
    result.final_avg_price = avg_price
    result.final_price = final_price
    return result


# ------------------- 리포트 -------------------
def print_report(r: Result) -> None:
    bh = (r.final_price / float(_first_valid_price)) - 1 if _first_valid_price else 0
    print()
    print(f"━━━ {r.ticker} 백테스트 결과 ━━━")
    print(f"  시작자본    : {r.start_krw:>14,.0f} 원")
    print(f"  종료자본    : {r.end_value:>14,.0f} 원")
    print(f"  총 수익률   : {r.total_return_pct:>13,.2f} %")
    print(f"  Buy&Hold 비교: {bh*100:>13,.2f} %")
    print(f"  실현 손익   : {r.realized_pnl:>+14,.0f} 원")
    print(f"  최대낙폭(MDD): {r.mdd_pct:>13,.2f} %")
    print(f"  거래 수     : {r.buy_count} buy / {r.sell_count} sell")
    print(f"  승률(매도기준): {r.win_rate:>13,.1f} %")
    if r.final_position_volume > 0:
        print(f"  잔여 포지션 : {r.final_position_volume:.6f} (avg {r.final_avg_price:,.0f} 원)")


_first_valid_price = 0.0


def run_one(ticker: str, args) -> Result:
    global _first_valid_price
    df = pyupbit.get_ohlcv(ticker, interval=args.interval, count=args.count)
    if df is None or df.empty:
        raise RuntimeError(f"{ticker} 캔들 조회 실패")
    _first_valid_price = float(df["close"].iloc[20]) if len(df) > 20 else float(df["close"].iloc[0])

    signal_fn = ai_signal_factory() if args.mode == "ai" else None
    return simulate(
        ticker, df,
        start_krw=args.capital,
        min_confidence=args.min_conf,
        max_order_krw=args.max_order,
        signal_fn=signal_fn,
        mode=args.mode,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="stockagent 백테스트")
    parser.add_argument("tickers", nargs="+", help="예: KRW-BTC KRW-SOL")
    parser.add_argument("--interval", default="minute60",
                        help="minute1/3/5/15/60, day 등 (기본 minute60)")
    parser.add_argument("--count", type=int, default=500, help="가져올 캔들 수")
    parser.add_argument("--capital", type=float, default=1_000_000, help="시작 자본(원)")
    parser.add_argument("--max-order", type=float, default=100_000,
                        help="1회 최대 주문금액(원)")
    parser.add_argument("--min-conf", type=float, default=config.MIN_CONFIDENCE)
    parser.add_argument("--mode", choices=["rule", "ai"], default="rule",
                        help="rule=RSI+MA 룰, ai=DecisionAgent 호출(느림·비용)")
    args = parser.parse_args()

    if args.mode == "ai":
        print(f"⚠ AI 모드: {args.count} 캔들마다 LLM 호출 → 시간/비용 주의")
        config.validate()

    for ticker in args.tickers:
        try:
            r = run_one(ticker, args)
            print_report(r)
        except Exception as e:  # noqa: BLE001
            print(f"❌ {ticker}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
