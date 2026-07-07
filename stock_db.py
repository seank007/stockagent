"""주식 자동매매 영속화 계층 (SQLite, 코인 DB와 분리).

코인(stockagent.db)과 완전히 분리된 stock_trading.db 를 쓴다. 실거래 중인
코인 손익·대시보드 쿼리에 어떤 영향도 주지 않기 위한 격리다.

저장 대상:
- decisions : AI 판단 기록 (코인과 동일 구조 + 종목명)
- trades    : 체결 기록 (paper/실계좌 모두, paper 여부는 trades.paper)
- positions : 종목별 수량/평단 (페이퍼 모드 체결 원장, KIS 연결 후엔 잔고 API가 원본)
- daily_pnl : 일자별 실현손익
- meta      : 가상 예수금 등 (페이퍼 모드)

수수료 모델(페이퍼): 위탁수수료 0.015% 양방향 + 매도 시 증권거래세 0.18%.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, date
from typing import Optional

DB_PATH = os.getenv("STOCK_DB_PATH") or os.path.join(os.path.dirname(__file__), "stock_trading.db")

PAPER_INITIAL_CASH = float(os.getenv("STOCK_PAPER_CASH", "10000000"))  # 가상 예수금 1천만원
COMMISSION_RATE = 0.00015   # 위탁수수료 0.015%
SELL_TAX_RATE = 0.0018      # 증권거래세(코스피 기준) 0.18%

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            code        TEXT NOT NULL,
            name        TEXT,
            price       REAL,
            rsi         REAL,
            trend       TEXT,
            change_pct  REAL,
            action      TEXT,
            confidence  REAL,
            reasoning   TEXT,
            order_side  TEXT,
            order_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_sdecisions_ts ON decisions(ts DESC);

        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            code        TEXT NOT NULL,
            name        TEXT,
            side        TEXT NOT NULL,            -- buy / sell
            price       REAL NOT NULL,
            qty         INTEGER NOT NULL,
            krw_amount  REAL NOT NULL,
            fee         REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            paper       INTEGER NOT NULL,          -- 1=페이퍼, 0=실계좌(KIS)
            raw_result  TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_strades_ts ON trades(ts DESC);

        CREATE TABLE IF NOT EXISTS positions (
            code        TEXT PRIMARY KEY,
            name        TEXT,
            qty         INTEGER NOT NULL DEFAULT 0,
            avg_price   REAL NOT NULL DEFAULT 0,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_pnl (
            day             TEXT PRIMARY KEY,
            realized_pnl    REAL NOT NULL DEFAULT 0,
            trades_count    INTEGER NOT NULL DEFAULT 0,
            buy_count       INTEGER NOT NULL DEFAULT 0,
            sell_count      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )


# ---------- 가상 예수금 (페이퍼 모드) ----------
def get_cash() -> float:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT value FROM meta WHERE key='cash'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('cash', ?)",
                         (str(PAPER_INITIAL_CASH),))
            return PAPER_INITIAL_CASH
        return float(row["value"])


def set_cash(value: float) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('cash', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(value),),
        )


# ---------- decisions ----------
def save_decision(code: str, name: str, price: float | None, rsi: float | None,
                  trend: str | None, change_pct: float | None, action: str,
                  confidence: float, reasoning: str, order_side: str = "none",
                  order_reason: str = "") -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO decisions
               (ts, code, name, price, rsi, trend, change_pct,
                action, confidence, reasoning, order_side, order_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(timespec="seconds"), code, name, price, rsi,
             trend, change_pct, action, float(confidence), reasoning,
             order_side, order_reason),
        )


def recent_decisions(limit: int = 100) -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- trades & positions ----------
def record_trade(code: str, name: str, side: str, price: float, qty: int,
                 paper: bool, raw_result: str = "") -> dict:
    """체결 기록 + 포지션/예수금/일별손익 갱신. 페이퍼 모드는 예수금도 움직인다."""
    ts = datetime.now().isoformat(timespec="seconds")
    amount = price * qty
    fee = amount * COMMISSION_RATE + (amount * SELL_TAX_RATE if side == "sell" else 0.0)
    realized = 0.0

    with _lock:
        conn = _connect()
        pos = conn.execute(
            "SELECT qty, avg_price FROM positions WHERE code = ?", (code,)
        ).fetchone()
        cur_qty = int(pos["qty"]) if pos else 0
        cur_avg = float(pos["avg_price"]) if pos else 0.0

        if side == "buy":
            new_qty = cur_qty + qty
            new_avg = ((cur_qty * cur_avg) + amount) / new_qty if new_qty > 0 else 0.0
        elif side == "sell":
            new_qty = max(0, cur_qty - qty)
            new_avg = cur_avg if new_qty > 0 else 0.0
            realized = (price - cur_avg) * qty - fee if cur_avg > 0 else 0.0
        else:
            new_qty, new_avg = cur_qty, cur_avg

        conn.execute(
            """INSERT INTO positions (code, name, qty, avg_price, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, qty=excluded.qty,
                 avg_price=excluded.avg_price, updated_at=excluded.updated_at""",
            (code, name, new_qty, new_avg, ts),
        )
        conn.execute(
            """INSERT INTO trades
               (ts, code, name, side, price, qty, krw_amount, fee, realized_pnl, paper, raw_result)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, code, name, side, price, qty, amount, fee, realized,
             1 if paper else 0, raw_result),
        )

        today = date.today().isoformat()
        conn.execute(
            """INSERT INTO daily_pnl (day, realized_pnl, trades_count, buy_count, sell_count)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(day) DO UPDATE SET
                 realized_pnl = realized_pnl + excluded.realized_pnl,
                 trades_count = trades_count + 1,
                 buy_count = buy_count + excluded.buy_count,
                 sell_count = sell_count + excluded.sell_count""",
            (today, realized, 1 if side == "buy" else 0, 1 if side == "sell" else 0),
        )

        if paper:
            row = conn.execute("SELECT value FROM meta WHERE key='cash'").fetchone()
            cash = float(row["value"]) if row else PAPER_INITIAL_CASH
            cash = cash - amount - fee if side == "buy" else cash + amount - fee
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('cash', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(cash),),
            )

    return {"ts": ts, "code": code, "side": side, "price": price, "qty": qty,
            "krw_amount": amount, "fee": fee, "realized_pnl": realized}


def recent_trades(limit: int = 50) -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def positions() -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM positions WHERE qty > 0 ORDER BY qty * avg_price DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_pnl(days: int = 30) -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM daily_pnl ORDER BY day DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


def total_realized_pnl() -> float:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) v FROM daily_pnl").fetchone()
        return float(row["v"])
