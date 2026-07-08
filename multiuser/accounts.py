"""회원·세션·거래소 키 서비스 (멀티유저 공개 API).

여기만 import하면 회원가입/로그인/세션검증/키등록/키조회가 된다.
비밀번호는 해시(werkzeug pbkdf2)로만 저장하고, 거래소 시크릿은 vault로 암호화한다.
거래소 키는 등록 시 출금 권한이 없음을 검증한 것만 저장한다.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from . import db, vault
from .exchange import VerifyResult, verify_upbit_key

SESSION_TTL_HOURS = 24 * 14  # 2주
_HASH_METHOD = "pbkdf2:sha256"  # 이식성 우선(모든 OpenSSL 빌드에서 동작)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8


class AccountError(Exception):
    """사용자에게 보여줄 수 있는 계정 관련 오류."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------- 회원
def register(email: str, password: str, display_name: str | None = None) -> dict:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise AccountError("이메일 형식이 올바르지 않습니다.")
    if len(password or "") < _MIN_PASSWORD:
        raise AccountError(f"비밀번호는 최소 {_MIN_PASSWORD}자 이상이어야 합니다.")

    pw_hash = generate_password_hash(password, method=_HASH_METHOD)
    now = _iso(_now())
    conn = db.connection()
    with db.lock():
        exists = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if exists:
            raise AccountError("이미 가입된 이메일입니다.")
        cur = conn.execute(
            """INSERT INTO users (email, password_hash, display_name, created_at)
               VALUES (?, ?, ?, ?)""",
            (email, pw_hash, (display_name or "").strip() or None, now),
        )
        user_id = cur.lastrowid
    return _public_user(_get_user_row(user_id))


def authenticate(email: str, password: str) -> dict | None:
    email = (email or "").strip().lower()
    conn = db.connection()
    with db.lock():
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not row["is_active"]:
        # 타이밍 공격 완화: 존재하지 않아도 해시 검증 비용을 대략 맞춘다
        check_password_hash(generate_password_hash("x", method=_HASH_METHOD), password or "")
        return None
    if not check_password_hash(row["password_hash"], password or ""):
        return None
    with db.lock():
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(_now()), row["id"]))
    return _public_user(row)


def get_user(user_id: int) -> dict | None:
    row = _get_user_row(user_id)
    return _public_user(row) if row else None


# ---------------------------------------------------------------- 세션
def create_session(user_id: int, ttl_hours: int = SESSION_TTL_HOURS) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    conn = db.connection()
    with db.lock():
        conn.execute(
            """INSERT INTO sessions (token, user_id, created_at, expires_at)
               VALUES (?, ?, ?, ?)""",
            (token, user_id, _iso(now), _iso(now + timedelta(hours=ttl_hours))),
        )
    return token


def user_for_session(token: str) -> dict | None:
    if not token:
        return None
    conn = db.connection()
    with db.lock():
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.revoked = 0 AND s.expires_at > ? AND u.is_active = 1""",
            (token, _iso(_now())),
        ).fetchone()
    return _public_user(row) if row else None


def revoke_session(token: str) -> None:
    conn = db.connection()
    with db.lock():
        conn.execute("UPDATE sessions SET revoked = 1 WHERE token = ?", (token,))


# ---------------------------------------------------------------- 거래소 키
def add_exchange_credential(
    user_id: int,
    access_key: str,
    secret_key: str,
    *,
    exchange: str = "upbit",
    label: str = "default",
    verify: bool = True,
) -> dict:
    """사용자 거래소 키 등록.

    verify=True면 유효성 + 출금권한 없음을 확인하고, 출금 권한이 있으면 거부한다.
    (테스트/특수 상황에서만 verify=False; 실서비스는 항상 True)
    """
    access_key = (access_key or "").strip()
    secret_key = (secret_key or "").strip()
    if not access_key or not secret_key:
        raise AccountError("access/secret 키를 모두 입력하세요.")
    if exchange != "upbit":
        raise AccountError(f"아직 지원하지 않는 거래소입니다: {exchange}")

    verified = False
    if verify:
        result: VerifyResult = verify_upbit_key(access_key, secret_key)
        if not result.valid:
            raise AccountError(result.detail)
        if result.can_withdraw:
            # 보안 정책: 출금 권한 키는 저장 자체를 거부
            raise AccountError(result.detail)
        verified = True

    now = _iso(_now())
    row = {
        "access_key_masked": _mask(access_key),
        "access_key_enc": vault.encrypt(access_key),
        "secret_key_enc": vault.encrypt(secret_key),
        "permission_verified": 1 if verified else 0,
        "verified_at": now if verified else None,
    }
    conn = db.connection()
    with db.lock():
        conn.execute(
            """INSERT INTO exchange_credentials
                 (user_id, exchange, label, access_key_masked, access_key_enc,
                  secret_key_enc, permission_verified, verified_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, exchange, label) DO UPDATE SET
                 access_key_masked=excluded.access_key_masked,
                 access_key_enc=excluded.access_key_enc,
                 secret_key_enc=excluded.secret_key_enc,
                 permission_verified=excluded.permission_verified,
                 verified_at=excluded.verified_at""",
            (
                user_id, exchange, label, row["access_key_masked"], row["access_key_enc"],
                row["secret_key_enc"], row["permission_verified"], row["verified_at"], now,
            ),
        )
    return {
        "exchange": exchange,
        "label": label,
        "access_key_masked": row["access_key_masked"],
        "permission_verified": bool(verified),
    }


def list_credentials(user_id: int) -> list[dict]:
    """시크릿은 절대 노출하지 않고 메타데이터만 반환."""
    conn = db.connection()
    with db.lock():
        rows = conn.execute(
            """SELECT exchange, label, access_key_masked, permission_verified, verified_at, created_at
               FROM exchange_credentials WHERE user_id = ? ORDER BY id""",
            (user_id,),
        ).fetchall()
    return [
        {
            "exchange": r["exchange"],
            "label": r["label"],
            "access_key_masked": r["access_key_masked"],
            "permission_verified": bool(r["permission_verified"]),
            "verified_at": r["verified_at"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_decrypted_credential(
    user_id: int, exchange: str = "upbit", label: str = "default"
) -> dict | None:
    """거래 엔진 전용: 복호화된 키를 반환. 로그/응답에 절대 노출 금지."""
    conn = db.connection()
    with db.lock():
        row = conn.execute(
            """SELECT access_key_enc, secret_key_enc FROM exchange_credentials
               WHERE user_id = ? AND exchange = ? AND label = ?""",
            (user_id, exchange, label),
        ).fetchone()
    if not row:
        return None
    return {
        "access_key": vault.decrypt(row["access_key_enc"]),
        "secret_key": vault.decrypt(row["secret_key_enc"]),
    }


def delete_credential(user_id: int, exchange: str = "upbit", label: str = "default") -> bool:
    conn = db.connection()
    with db.lock():
        cur = conn.execute(
            "DELETE FROM exchange_credentials WHERE user_id = ? AND exchange = ? AND label = ?",
            (user_id, exchange, label),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------- helpers
def _get_user_row(user_id: int):
    conn = db.connection()
    with db.lock():
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _public_user(row) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
    }


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"
