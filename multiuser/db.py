"""멀티테넌트 영속화 (SQLite, 단일 봇 DB와 분리).

테이블
- users                : 회원 (이메일 + 비번 해시)
- sessions             : 로그인 세션 토큰
- exchange_credentials : 사용자별 거래소 키(시크릿은 vault로 암호화 저장)

단일 사용자 봇의 stockagent.db와는 별도 파일(multiuser.db)을 쓴다.
거래/판단 이력의 멀티테넌트화(각 테이블에 user_id 추가)는 다음 단계 —
여기서는 회원/인증/키관리라는 보안 핵심 토대만 담는다.
"""
from __future__ import annotations

import os
import sqlite3
import threading

DB_PATH = os.getenv("MULTIUSER_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "multiuser.db"
)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_dir = os.path.dirname(os.path.abspath(DB_PATH))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name  TEXT,
            is_active     INTEGER NOT NULL DEFAULT 1,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            last_login_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            revoked     INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

        CREATE TABLE IF NOT EXISTS exchange_credentials (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL,
            exchange            TEXT NOT NULL DEFAULT 'upbit',
            label               TEXT NOT NULL DEFAULT 'default',
            access_key_masked   TEXT NOT NULL,   -- 앞뒤 일부만 (표시용)
            access_key_enc      TEXT NOT NULL,   -- vault 암호문
            secret_key_enc      TEXT NOT NULL,   -- vault 암호문
            permission_verified INTEGER NOT NULL DEFAULT 0,  -- 출금권한 없음 확인됨
            verified_at         TEXT,
            created_at          TEXT NOT NULL,
            UNIQUE (user_id, exchange, label),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_cred_user ON exchange_credentials(user_id);

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id      INTEGER PRIMARY KEY,
            auto_enabled INTEGER NOT NULL DEFAULT 0,   -- 자동매매 on/off (기본 off)
            dry_run      INTEGER NOT NULL DEFAULT 1,    -- 모의매매 (기본 on = 안전)
            tickers      TEXT NOT NULL DEFAULT 'KRW-BTC,KRW-ETH',
            max_order_krw INTEGER NOT NULL DEFAULT 10000,
            updated_at   TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
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
            order_reason TEXT,
            dry_run     INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_udec_user ON user_decisions(user_id, id DESC);

        CREATE TABLE IF NOT EXISTS user_trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            ts          TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            side        TEXT NOT NULL,
            price       REAL NOT NULL,
            volume      REAL NOT NULL,
            krw_amount  REAL NOT NULL,
            dry_run     INTEGER NOT NULL DEFAULT 1,
            raw_result  TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_utr_user ON user_trades(user_id, id DESC);
        """
    )


def connection() -> sqlite3.Connection:
    return _connect()


def lock() -> threading.Lock:
    return _lock


def reset_for_tests() -> None:
    """테스트에서 커넥션 캐시를 비운다(DB_PATH를 바꾼 뒤 호출)."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn = None
