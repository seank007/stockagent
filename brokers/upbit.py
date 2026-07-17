"""업비트 연동 래퍼.

pyupbit를 감싸서 stockagent가 쓰는 형태로 잔고 조회/시세/지표/주문을 제공한다.
DRY_RUN일 때는 주문을 실제로 내지 않고 모의 결과를 반환한다.
"""
from __future__ import annotations

from typing import Optional

import pyupbit

import config


class UpbitBroker:
    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        # 멀티유저: 인자로 키를 주면 그 사용자 키로, 아니면 기존처럼 config(단일 봇) 키로.
        access_key = access_key if access_key is not None else config.UPBIT_ACCESS_KEY
        secret_key = secret_key if secret_key is not None else config.UPBIT_SECRET_KEY
        # 모의매매면 키 없이도 시세 조회는 가능하므로 클라이언트는 키가 있을 때만 생성
        if access_key and secret_key:
            self.client: Optional[pyupbit.Upbit] = pyupbit.Upbit(access_key, secret_key)
        else:
            self.client = None

    # ---------- 시세 / 지표 ----------
    def market_snapshot(
        self,
        ticker: str,
        interval: str | None = None,
        count: int | None = None,
    ) -> dict:
        """Claude에게 넘길 시세 요약. 토큰 절약을 위해 핵심 지표만 계산해서 반환."""
        interval = interval or config.CANDLE_INTERVAL
        count = count or config.CANDLE_COUNT
        df = pyupbit.get_ohlcv(
            ticker, interval=interval, count=count
        )
        if df is None or df.empty:
            raise RuntimeError(f"{ticker} 캔들 조회 실패")

        close = df["close"]
        price = float(pyupbit.get_current_price(ticker))

        ma5 = float(close.tail(5).mean())
        ma20 = float(close.tail(20).mean())
        rsi14 = _rsi(close, 14)

        # 24시간(=캔들 간격 기준 환산) 대신 가져온 구간의 변동률
        change_pct = float((close.iloc[-1] / close.iloc[0] - 1) * 100)

        # 최근 5개 캔들만 간단히 (시/고/저/종/거래량)
        recent = [
            {
                "open": round(float(r.open), 2),
                "high": round(float(r.high), 2),
                "low": round(float(r.low), 2),
                "close": round(float(r.close), 2),
                "volume": round(float(r.volume), 4),
            }
            for r in df.tail(5).itertuples()
        ]

        return {
            "ticker": ticker,
            "price": price,
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "rsi14": round(rsi14, 2),
            "trend": "up" if ma5 > ma20 else "down",
            "period_change_pct": round(change_pct, 2),
            "candle_interval": interval,
            "recent_candles": recent,
        }

    # ---------- 잔고 ----------
    def get_krw_balance(self) -> float:
        if self.client is None:
            return 0.0
        bal = self.client.get_balance("KRW")
        return float(bal or 0.0)

    def get_coin_balance(self, ticker: str) -> float:
        """해당 코인 보유 수량."""
        if self.client is None:
            return 0.0
        bal = self.client.get_balance(ticker)
        return float(bal or 0.0)

    def get_avg_buy_price(self, ticker: str) -> float:
        if self.client is None:
            return 0.0
        price = self.client.get_avg_buy_price(ticker)
        return float(price or 0.0)

    def get_balances(self) -> list[dict]:
        if self.client is None:
            return []
        balances = self.client.get_balances()
        return _checked_balances(balances)

    @staticmethod
    def krw_from_balances(balances: list[dict]) -> float:
        for balance in balances:
            if balance.get("currency") == "KRW":
                # locked 금액은 이미 다른 주문에 예약되어 새 주문에 사용할 수 없다.
                return float(balance.get("balance", 0) or 0)
        return 0.0

    @staticmethod
    def position_from_balances(ticker: str, balances: list[dict]) -> tuple[float, float]:
        currency = ticker.replace("KRW-", "")
        for balance in balances:
            if balance.get("currency") != currency:
                continue
            free = float(balance.get("balance", 0) or 0)
            avg_buy_price = float(balance.get("avg_buy_price", 0) or 0)
            # 매도 가능 수량에도 기존 주문에 묶인 locked 수량을 포함하지 않는다.
            return free, avg_buy_price
        return 0.0, 0.0

    def get_portfolio(self, balances: list[dict] | None = None) -> dict:
        """현재 계좌의 전체 종목, 투자 원금, 현재 가치, 비중 등을 담아 반환"""
        if self.client is None:
            return {"total_principal": 0.0, "total_value": 0.0, "items": []}

        balances = self.get_balances() if balances is None else balances
        if not balances:
            return {"total_principal": 0.0, "total_value": 0.0, "items": []}
        
        tickers_to_fetch = []
        items_temp = []
        total_principal = 0.0
        
        # 1. 잔고가 있는 화폐 수집 및 원금 합산
        for b in balances:
            currency = b.get("currency")
            balance = float(b.get("balance", 0))
            locked = float(b.get("locked", 0))
            total_bal = balance + locked
            avg_buy_price = float(b.get("avg_buy_price", 0))
            
            if total_bal > 0:
                if currency == "KRW":
                    items_temp.append({
                        "currency": currency,
                        "ticker": "KRW",
                        "balance": total_bal,
                        "avg_buy_price": 1.0,
                        "principal": total_bal
                    })
                    total_principal += total_bal
                else:
                    ticker = f"KRW-{currency}"
                    tickers_to_fetch.append(ticker)
                    principal = total_bal * avg_buy_price
                    items_temp.append({
                        "currency": currency,
                        "ticker": ticker,
                        "balance": total_bal,
                        "avg_buy_price": avg_buy_price,
                        "principal": principal
                    })
                    total_principal += principal
                    
        # 2. 현재가 일괄 조회 (KRW 마켓)
        current_prices = {}
        if tickers_to_fetch:
            prices = pyupbit.get_current_price(tickers_to_fetch)
            if isinstance(prices, dict):
                current_prices = prices
            elif len(tickers_to_fetch) == 1 and prices is not None:
                current_prices = {tickers_to_fetch[0]: float(prices)}
                
        items = []
        total_value = 0.0
        
        # 3. 현재 가치 및 수익률 계산
        for item in items_temp:
            if item["currency"] == "KRW":
                current_val = item["principal"]
                current_price = 1.0
            else:
                current_price = current_prices.get(item["ticker"], item["avg_buy_price"])
                current_val = item["balance"] * current_price
                
            item["current_price"] = current_price
            item["current_value"] = current_val
            item["return_pct"] = ((current_val / item["principal"]) - 1) * 100 if item["principal"] > 0 else 0
            
            total_value += current_val
            items.append(item)
            
        # 4. 비중 계산 및 가치순 정렬
        for item in items:
            item["weight"] = (item["current_value"] / total_value * 100) if total_value > 0 else 0
            
        items.sort(key=lambda x: x["current_value"], reverse=True)
        
        return {
            "total_principal": total_principal,
            "total_value": total_value,
            "total_return_pct": ((total_value / total_principal) - 1) * 100 if total_principal > 0 else 0,
            "items": items
        }

    # ---------- 주문 ----------
    def buy(self, ticker: str, krw_amount: float) -> dict:
        if config.DRY_RUN:
            return {"dry_run": True, "side": "buy", "ticker": ticker, "krw": krw_amount}
        client = self._live_client()
        return _checked_order(client.buy_market_order(ticker, krw_amount))

    def sell(self, ticker: str, volume: float) -> dict:
        if config.DRY_RUN:
            return {"dry_run": True, "side": "sell", "ticker": ticker, "volume": volume}
        client = self._live_client()
        return _checked_order(client.sell_market_order(ticker, volume))

    def _live_client(self):
        """Enforce the live-trading interlock at the final exchange boundary."""
        if not config.ALLOW_LIVE_TRADING:
            raise RuntimeError("실거래 차단됨: ALLOW_LIVE_TRADING=true가 필요합니다.")
        if self.client is None:
            raise RuntimeError("실거래 차단됨: 유효한 업비트 API 키가 없습니다.")
        return self.client


def _rsi(close, period: int = 14) -> float:
    """단순 RSI. 데이터가 부족하면 50(중립) 반환."""
    if len(close) < period + 1:
        return 50.0
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).tail(period).mean()
    loss = -delta.clip(upper=0).tail(period).mean()
    if loss == 0:
        return 100.0
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _checked_balances(balances) -> list[dict]:
    if isinstance(balances, dict) and balances.get("error"):
        err = balances.get("error") or {}
        name = err.get("name") or "upbit_error"
        message = err.get("message") or "업비트 계좌 조회 실패"
        raise RuntimeError(f"{name}: {message}")
    if not isinstance(balances, list):
        raise RuntimeError(f"업비트 계좌 조회 응답 형식 오류: {type(balances).__name__}")
    return balances


def _checked_order(result) -> dict:
    """Reject exchange errors before they can be persisted as successful trades."""
    if isinstance(result, dict) and result.get("error"):
        err = result.get("error") or {}
        name = err.get("name") or "upbit_order_error"
        message = err.get("message") or "업비트 주문 접수 실패"
        raise RuntimeError(f"{name}: {message}")
    if not isinstance(result, dict) or not result.get("uuid"):
        raise RuntimeError("업비트 주문 접수 응답에 주문 UUID가 없습니다.")
    return result
