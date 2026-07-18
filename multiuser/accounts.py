"""회원·세션·거래소 키 서비스 (멀티유저 공개 API).

여기만 import하면 회원가입/로그인/세션검증/키등록/키조회가 된다.
비밀번호는 해시(werkzeug pbkdf2)로만 저장하고, 거래소 시크릿은 vault로 암호화한다.
거래소 키는 등록 시 출금 권한이 없음을 검증한 것만 저장한다.
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from . import db, vault
from .exchange import VerifyResult, verify_upbit_key

SESSION_TTL_HOURS = 24 * 14  # 2주
_HASH_METHOD = "pbkdf2:sha256"  # 이식성 우선(모든 OpenSSL 빌드에서 동작)
_EMAIL_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_MIN_PASSWORD = 8

# 운영자 이메일은 일반 가입에서 예약하고, 별도 bootstrap token으로만 생성/승격한다.
_ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
_ADMIN_BOOTSTRAP_TOKEN = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()


def _is_admin_email(email: str) -> bool:
    return (email or "").lower() in _ADMIN_EMAILS


class AccountError(Exception):
    """사용자에게 보여줄 수 있는 계정 관련 오류."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------- 회원
def register(email: str, password: str, display_name: str | None = None) -> dict:
    email = (email or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise AccountError("이메일 형식이 올바르지 않습니다.")
    if _is_admin_email(email):
        raise AccountError("관리자 예약 이메일은 일반 회원가입을 사용할 수 없습니다.")
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
            """INSERT INTO users (email, password_hash, display_name, is_admin, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (email, pw_hash, (display_name or "").strip() or None, 0, now),
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
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (_iso(_now()), row["id"]),
        )
    return _public_user(_get_user_row(row["id"]))


def bootstrap_admin(
    email: str,
    password: str,
    bootstrap_token: str,
    display_name: str | None = None,
) -> dict:
    """Create or promote an admin only with the out-of-band bootstrap secret.

    This function is intentionally not exposed as an HTTP route. Use the local
    ``scripts/create_admin.py`` command in a trusted deployment shell.
    """
    email = (email or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
        raise AccountError("이메일 형식이 올바르지 않습니다.")
    if not _is_admin_email(email):
        raise AccountError("ADMIN_EMAILS에 등록된 이메일만 관리자로 만들 수 있습니다.")
    if len(_ADMIN_BOOTSTRAP_TOKEN) < 32:
        raise AccountError("ADMIN_BOOTSTRAP_TOKEN을 32자 이상으로 설정하세요.")
    if not secrets.compare_digest(str(bootstrap_token or ""), _ADMIN_BOOTSTRAP_TOKEN):
        raise AccountError("관리자 부트스트랩 토큰이 올바르지 않습니다.")
    if len(password or "") < _MIN_PASSWORD:
        raise AccountError(f"비밀번호는 최소 {_MIN_PASSWORD}자 이상이어야 합니다.")

    conn = db.connection()
    now = _iso(_now())
    with db.lock():
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            if not check_password_hash(row["password_hash"], password or ""):
                raise AccountError("기존 계정 비밀번호가 올바르지 않습니다.")
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (row["id"],))
            user_id = row["id"]
        else:
            pw_hash = generate_password_hash(password, method=_HASH_METHOD)
            cur = conn.execute(
                """INSERT INTO users (email, password_hash, display_name, is_admin, created_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (email, pw_hash, (display_name or "").strip() or None, now),
            )
            user_id = cur.lastrowid
    return _public_user(_get_user_row(user_id))


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


# ---------------------------------------------------------------- 내 계정 설정
def change_password(user_id: int, current_password: str, new_password: str) -> None:
    if len(new_password or "") < _MIN_PASSWORD:
        raise AccountError(f"새 비밀번호는 최소 {_MIN_PASSWORD}자 이상이어야 합니다.")
    row = _get_user_row(user_id)
    if not row or not check_password_hash(row["password_hash"], current_password or ""):
        raise AccountError("현재 비밀번호가 올바르지 않습니다.")
    new_hash = generate_password_hash(new_password, method=_HASH_METHOD)
    conn = db.connection()
    with db.lock():
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        # 보안: 비번 변경 시 다른 모든 세션 폐기(현재 세션도 → 재로그인 유도)
        conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))


def update_profile(user_id: int, display_name: str | None) -> dict:
    conn = db.connection()
    with db.lock():
        conn.execute(
            "UPDATE users SET display_name = ? WHERE id = ?",
            ((display_name or "").strip() or None, user_id),
        )
    return _public_user(_get_user_row(user_id))


def delete_account(user_id: int, password: str) -> None:
    """본인 탈퇴. 비밀번호 확인 후 계정+키+세션+설정 전부 삭제(FK CASCADE)."""
    row = _get_user_row(user_id)
    if not row or not check_password_hash(row["password_hash"], password or ""):
        raise AccountError("비밀번호가 올바르지 않습니다.")
    conn = db.connection()
    with db.lock():
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------------------------------------------------------------- 운영자(admin)
def list_all_users() -> list[dict]:
    """운영자용 전체 회원 목록. 비번/시크릿은 절대 포함하지 않는다."""
    conn = db.connection()
    with db.lock():
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, u.is_admin, u.is_active,
                      u.created_at, u.last_login_at,
                      (SELECT COUNT(*) FROM exchange_credentials c WHERE c.user_id = u.id) AS key_count,
                      COALESCE(s.auto_enabled, 0) AS auto_enabled
               FROM users u LEFT JOIN user_settings s ON s.user_id = u.id
               ORDER BY u.id"""
        ).fetchall()
    return [
        {
            "id": r["id"], "email": r["email"], "display_name": r["display_name"],
            "is_admin": bool(r["is_admin"]), "is_active": bool(r["is_active"]),
            "created_at": r["created_at"], "last_login_at": r["last_login_at"],
            "has_key": bool(r["key_count"]), "auto_enabled": bool(r["auto_enabled"]),
        }
        for r in rows
    ]


def set_user_active(user_id: int, active: bool) -> None:
    conn = db.connection()
    with db.lock():
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
        if not active:
            conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))


def admin_delete_user(user_id: int) -> None:
    conn = db.connection()
    with db.lock():
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


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
        "subscription_tier": row["subscription_tier"] if "subscription_tier" in row.keys() else "free",
        "created_at": row["created_at"],
    }


def _mask(key: str) -> str:
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"
