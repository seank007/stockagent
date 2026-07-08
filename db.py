"""stockagent 영속화 계층 (SQLite).

저장 대상:
- decisions : 모든 AI 판단 기록 (시각/티커/가격/지표/판단/신뢰도/근거/주문결과)
- trades    : 실제 체결된 매수/매도 (실거래·DRY_RUN 모두 기록)
- positions : 종목별 평균단가/수량 (체결 시 갱신, 실현손익 계산용)
- daily_pnl : 일자별 실현손익·매매횟수

재시작해도 손익·이력이 유지된다. DRY_RUN 여부는 trades.dry_run 필드에 함께 저장.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, date
from typing import Optional

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(__file__), "stockagent.db")

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_dir = os.path.dirname(os.path.abspath(DB_PATH))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
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
            ticker      TEXT NOT NULL,
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
        CREATE INDEX IF NOT EXISTS ix_decisions_ts ON decisions(ts DESC);

        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            side        TEXT NOT NULL,            -- buy / sell
            price       REAL NOT NULL,
            volume      REAL NOT NULL,
            krw_amount  REAL NOT NULL,
            fee         REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,          -- 매도 시 실현손익 (원)
            dry_run     INTEGER NOT NULL,         -- 1 / 0
            raw_result  TEXT                       -- 거래소 응답 JSON
        );
        CREATE INDEX IF NOT EXISTS ix_trades_ts ON trades(ts DESC);
        CREATE INDEX IF NOT EXISTS ix_trades_ticker ON trades(ticker);

        CREATE TABLE IF NOT EXISTS positions (
            ticker      TEXT PRIMARY KEY,
            volume      REAL NOT NULL DEFAULT 0,
            avg_price   REAL NOT NULL DEFAULT 0,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_pnl (
            day             TEXT PRIMARY KEY,      -- YYYY-MM-DD
            realized_pnl    REAL NOT NULL DEFAULT 0,
            trades_count    INTEGER NOT NULL DEFAULT 0,
            buy_count       INTEGER NOT NULL DEFAULT 0,
            sell_count      INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS manual_portfolio (
            ticker          TEXT PRIMARY KEY,      -- KRW or KRW-XXX
            currency        TEXT NOT NULL,
            balance         REAL NOT NULL DEFAULT 0,
            avg_buy_price   REAL NOT NULL DEFAULT 0,
            updated_at      TEXT
        );
        """
    )


# ---------- decisions ----------
def save_decision(ticker: str, snapshot: dict, decision, order) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO decisions
               (ts, ticker, price, rsi, trend, change_pct,
                action, confidence, reasoning, order_side, order_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(timespec="seconds"),
                ticker,
                snapshot.get("price"),
                snapshot.get("rsi14"),
                snapshot.get("trend"),
                snapshot.get("period_change_pct"),
                decision.action,
                float(decision.confidence),
                decision.reasoning,
                order.side,
                order.reason,
            ),
        )


def recent_decisions(limit: int = 100, ticker: str | None = None, action: str | None = None) -> list[dict]:
    with _lock:
        conn = _connect()
        where = []
        params: list[object] = []
        if ticker:
            where.append("ticker = ?")
            params.append(ticker)
        if action:
            where.append("action = ?")
            params.append(action)
        sql = "SELECT * FROM decisions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ---------- trades & positions ----------
def record_trade(
    ticker: str,
    side: str,
    price: float,
    volume: float,
    krw_amount: float,
    dry_run: bool,
    raw_result: str = "",
) -> dict:
    """체결 기록 + 포지션 갱신 + 실현손익 계산. 결과 dict 반환."""
    ts = datetime.now().isoformat(timespec="seconds")
    realized = 0.0
    fee_rate = 0.0005  # 업비트 매매수수료 0.05% (대략)
    fee = krw_amount * fee_rate

    with _lock:
        conn = _connect()
        pos = conn.execute(
            "SELECT volume, avg_price FROM positions WHERE ticker = ?", (ticker,)
        ).fetchone()
        cur_vol = float(pos["volume"]) if pos else 0.0
        cur_avg = float(pos["avg_price"]) if pos else 0.0

        if side == "buy":
            new_vol = cur_vol + volume
            new_avg = (
                ((cur_vol * cur_avg) + (volume * price)) / new_vol if new_vol > 0 else 0.0
            )
        elif side == "sell":
            new_vol = max(0.0, cur_vol - volume)
            new_avg = cur_avg if new_vol > 0 else 0.0
            realized = (price - cur_avg) * volume - fee if cur_avg > 0 else 0.0
        else:
            new_vol, new_avg = cur_vol, cur_avg

        conn.execute(
            """INSERT INTO positions (ticker, volume, avg_price, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 volume=excluded.volume,
                 avg_price=excluded.avg_price,
                 updated_at=excluded.updated_at""",
            (ticker, new_vol, new_avg, ts),
        )
        conn.execute(
            """INSERT INTO trades
               (ts, ticker, side, price, volume, krw_amount, fee, realized_pnl, dry_run, raw_result)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ts, ticker, side, price, volume, krw_amount,
                fee, realized, 1 if dry_run else 0, raw_result,
            ),
        )

        today = date.today().isoformat()
        conn.execute(
            """INSERT INTO daily_pnl (day, realized_pnl, trades_count, buy_count, sell_count)
               VALUES (?, ?, 1, ?, ?)
               ON CONFLICT(day) DO UPDATE SET
                 realized_pnl = realized_pnl + excluded.realized_pnl,
                 trades_count = trades_count + 1,
                 buy_count    = buy_count + excluded.buy_count,
                 sell_count   = sell_count + excluded.sell_count""",
            (today, realized, 1 if side == "buy" else 0, 1 if side == "sell" else 0),
        )

    return {"realized_pnl": realized, "fee": fee, "new_volume": new_vol, "new_avg": new_avg}


def get_today_realized_pnl() -> float:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT realized_pnl FROM daily_pnl WHERE day = ?", (date.today().isoformat(),)
        ).fetchone()
        return float(row["realized_pnl"]) if row else 0.0


def get_daily_pnl(days: int = 30) -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM daily_pnl ORDER BY day DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


def performance_stats() -> dict:
    """체결 이력으로 성과 지표를 계산한다.

    매도 체결의 realized_pnl로 승률·손익비·MDD(누적손익 기준)를,
    전체 체결로 거래 수·수수료 합계를 낸다.
    """
    with _lock:
        conn = _connect()
        sells = conn.execute(
            "SELECT realized_pnl, ts FROM trades WHERE side = 'sell' ORDER BY id ASC"
        ).fetchall()
        totals = conn.execute(
            "SELECT COUNT(*) AS n, "
            "COALESCE(SUM(fee), 0) AS fees, "
            "COALESCE(SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END), 0) AS buys, "
            "COALESCE(SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END), 0) AS sells "
            "FROM trades"
        ).fetchone()

    pnls = [float(r["realized_pnl"] or 0.0) for r in sells]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    closed = len(pnls)
    total_realized = sum(pnls)

    # 누적 실현손익 곡선의 최대 낙폭(MDD)
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)

    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "trades_total": int(totals["n"]),
        "buys": int(totals["buys"]),
        "sells": int(totals["sells"]),
        "closed_trades": closed,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / closed * 100.0) if closed else 0.0,
        "total_realized_pnl": total_realized,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "best_trade": max(pnls) if pnls else 0.0,
        "worst_trade": min(pnls) if pnls else 0.0,
        "max_drawdown": mdd,          # 0 또는 음수
        "total_fees": float(totals["fees"]),
    }


def recent_trades(limit: int = 50, ticker: str | None = None, side: str | None = None) -> list[dict]:
    with _lock:
        conn = _connect()
        where = []
        params: list[object] = []
        if ticker:
            where.append("ticker = ?")
            params.append(ticker)
        if side:
            where.append("side = ?")
            params.append(side)
        sql = "SELECT * FROM trades"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_position(ticker: str) -> dict:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM positions WHERE ticker = ?", (ticker,)
        ).fetchone()
        return dict(row) if row else {"ticker": ticker, "volume": 0.0, "avg_price": 0.0}


def all_positions() -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM positions ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def manual_portfolio_items() -> list[dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM manual_portfolio ORDER BY CASE WHEN ticker = 'KRW' THEN 0 ELSE 1 END, ticker"
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_manual_portfolio_item(ticker: str, balance: float, avg_buy_price: float) -> dict:
    ticker = ticker.upper().strip()
    currency = "KRW" if ticker == "KRW" else ticker.replace("KRW-", "")
    ts = datetime.now().isoformat(timespec="seconds")
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO manual_portfolio (ticker, currency, balance, avg_buy_price, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 currency=excluded.currency,
                 balance=excluded.balance,
                 avg_buy_price=excluded.avg_buy_price,
                 updated_at=excluded.updated_at""",
            (ticker, currency, balance, avg_buy_price, ts),
        )
    return {
        "ticker": ticker,
        "currency": currency,
        "balance": balance,
        "avg_buy_price": avg_buy_price,
        "updated_at": ts,
    }


def delete_manual_portfolio_item(ticker: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM manual_portfolio WHERE ticker = ?",
            (ticker.upper().strip(),),
        )
        return cur.rowcount > 0


def trade_stats_by_ticker() -> dict[str, dict]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """SELECT ticker,
                      COUNT(*) AS trades_count,
                      SUM(CASE WHEN side = 'buy' THEN 1 ELSE 0 END) AS buy_count,
                      SUM(CASE WHEN side = 'sell' THEN 1 ELSE 0 END) AS sell_count,
                      COALESCE(SUM(realized_pnl), 0) AS realized_pnl,
                      MAX(ts) AS last_trade_at
               FROM trades
               GROUP BY ticker"""
        ).fetchall()
        return {r["ticker"]: dict(r) for r in rows}


def total_realized_pnl() -> float:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) AS s FROM trades").fetchone()
        return float(row["s"]) if row else 0.0
