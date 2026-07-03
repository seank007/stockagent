"""대시보드용 공유 상태 저장소.

매매 루프(백그라운드 스레드)가 여기에 기록하고, 웹 서버가 읽어서 브라우저에 보여준다.
판단/거래 영속화는 db.py가 담당하며, 기동 시 hydrate_from_db()로 최근 이력을 복원한다.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

import config


class Store:
    def __init__(self, max_history: int | None = None) -> None:
        self._lock = threading.Lock()
        max_history = max_history or int(getattr(config, "STATE_HISTORY_LIMIT", 80))
        self.started_at = datetime.now()
        self.mode = "DRY_RUN(모의)" if config.DRY_RUN else "실거래"
        self.provider = config.AI_PROVIDER
        self.krw_balance = 0.0
        self.tickers: dict[str, dict] = {}
        self.history: deque[dict] = deque(maxlen=max_history)
        self.portfolio: dict = {}
        self.last_update: datetime | None = None
        self.error: str | None = None
        self.today_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.paused = False
        self.loop_running = False
        self.cycle_count = 0
        self.last_cycle_started: datetime | None = None
        self.last_cycle_finished: datetime | None = None
        self.next_run_at: datetime | None = None

    def update_portfolio(self, portfolio_data: dict) -> None:
        with self._lock:
            self.portfolio = portfolio_data

    def update_ticker(self, ticker: str, snapshot: dict, decision, order) -> None:
        with self._lock:
            row = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "ticker": ticker,
                "price": snapshot.get("price"),
                "rsi": snapshot.get("rsi14"),
                "trend": snapshot.get("trend"),
                "change_pct": snapshot.get("period_change_pct"),
                "action": decision.action,
                "confidence": round(decision.confidence, 2),
                "reasoning": _compact_text(decision.reasoning),
                "order": f"{order.side} | {order.reason}",
            }
            self.tickers[ticker] = row
            self.history.appendleft(row)
            self.last_update = datetime.now()

    def set_krw(self, krw: float) -> None:
        with self._lock:
            self.krw_balance = krw

    def set_error(self, msg: str | None) -> None:
        with self._lock:
            self.error = msg

    def set_pnl(self, today_pnl: float, total_pnl: float) -> None:
        with self._lock:
            self.today_pnl = today_pnl
            self.total_pnl = total_pnl

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self.paused = paused

    def is_paused(self) -> bool:
        with self._lock:
            return self.paused

    def set_loop_running(self, running: bool) -> None:
        with self._lock:
            self.loop_running = running

    def mark_cycle_start(self) -> None:
        with self._lock:
            self.cycle_count += 1
            self.last_cycle_started = datetime.now()
            self.next_run_at = None

    def mark_cycle_end(self, next_run_at: datetime | None = None) -> None:
        with self._lock:
            self.last_cycle_finished = datetime.now()
            self.next_run_at = next_run_at

    def hydrate_from_db(self) -> None:
        """기동 시 최근 판단 기록을 DB에서 불러와 history를 복원."""
        import db
        rows = db.recent_decisions(limit=self.history.maxlen or 100)
        with self._lock:
            self.history.clear()
            for r in rows:
                ts = r.get("ts") or ""
                self.history.append({
                    "time": ts.split("T")[-1][:8] if "T" in ts else ts[-8:],
                    "ticker": r.get("ticker"),
                    "price": r.get("price"),
                    "rsi": r.get("rsi"),
                    "trend": r.get("trend"),
                    "change_pct": r.get("change_pct"),
                    "action": r.get("action"),
                    "confidence": round(float(r.get("confidence") or 0), 2),
                    "reasoning": _compact_text(r.get("reasoning")),
                    "order": f"{r.get('order_side')} | {r.get('order_reason')}",
                })
            self.today_pnl = db.get_today_realized_pnl()
            self.total_pnl = db.total_realized_pnl()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "provider": self.provider,
                "model": config.MODELS.get(self.provider, "?"),
                "krw_balance": self.krw_balance,
                "portfolio": self.portfolio,
                "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                "last_update": self.last_update.strftime("%H:%M:%S")
                if self.last_update
                else None,
                "interval": config.INTERVAL_SECONDS,
                "tickers": list(self.tickers.values()),
                "history": list(self.history),
                "today_pnl": self.today_pnl,
                "total_pnl": self.total_pnl,
                "error": self.error,
                "bot_paused": self.paused,
                "loop_running": self.loop_running,
                "cycle_count": self.cycle_count,
                "last_cycle_started": self.last_cycle_started.strftime("%Y-%m-%d %H:%M:%S")
                if self.last_cycle_started
                else None,
                "last_cycle_finished": self.last_cycle_finished.strftime("%Y-%m-%d %H:%M:%S")
                if self.last_cycle_finished
                else None,
                "next_run_at": self.next_run_at.strftime("%Y-%m-%d %H:%M:%S")
                if self.next_run_at
                else None,
            }


store = Store()


def _compact_text(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    max_chars = int(getattr(config, "STATE_REASON_MAX_CHARS", 260))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "..."
