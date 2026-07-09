"""저장 전 암호화(encryption at rest).

거래소 API 시크릿을 DB에 평문으로 두지 않기 위한 최소·안전한 래퍼.
Fernet(AES-128-CBC + HMAC-SHA256, 인증 암호화)을 쓴다.

마스터 키 우선순위
1. 환경변수 MULTIUSER_MASTER_KEY  (운영 필수 — KMS/시크릿 매니저에서 주입)
2. 파일  MULTIUSER_MASTER_KEY_FILE (없으면 db 옆 multiuser_master.key)
   - 파일이 없으면 개발 편의를 위해 자동 생성하되 큰 경고를 남긴다.

운영에서는 반드시 1번(env)로 주입하고, 키를 유출/버전관리에 올리지 말 것.
마스터 키가 바뀌면 기존 암호문은 복호화 불가 → 키 로테이션은 재암호화가 필요.
"""
from __future__ import annotations

import os
import stat
import sys
import threading

from cryptography.fernet import Fernet, InvalidToken

_ENV_KEY = "MULTIUSER_MASTER_KEY"
_ENV_KEY_FILE = "MULTIUSER_MASTER_KEY_FILE"

_lock = threading.Lock()
_fernet: Fernet | None = None


def generate_master_key() -> str:
    """새 마스터 키(urlsafe base64, 32바이트) 생성. 운영 주입용."""
    return Fernet.generate_key().decode("ascii")


def _default_key_file() -> str:
    override = os.getenv(_ENV_KEY_FILE)
    if override:
        return override
    from . import db as _db  # 지연 import (순환 방지)

    return os.path.join(os.path.dirname(os.path.abspath(_db.DB_PATH)), "multiuser_master.key")


def _load_or_create_key() -> bytes:
    env_val = os.getenv(_ENV_KEY)
    if env_val and env_val.strip():
        return env_val.strip().encode("ascii")

    path = _default_key_file()
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read().strip()

    # 개발 편의 폴백: 파일로 생성. 운영에서는 env로 주입해야 한다.
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass
    print(
        f"[multiuser.vault] ⚠️  마스터 키가 없어 새로 생성했습니다: {path}\n"
        f"    운영에서는 환경변수 {_ENV_KEY} 로 주입하세요. 이 파일을 유실/유출하면\n"
        f"    저장된 거래소 키를 복호화할 수 없습니다(백업·시크릿매니저 필수).",
        file=sys.stderr,
        flush=True,
    )
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _lock:
            if _fernet is None:
                _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """평문 → 암호문(문자열). 저장용."""
    if plaintext is None:
        raise ValueError("암호화할 값이 없습니다")
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """암호문 → 평문. 마스터 키가 다르거나 변조되면 InvalidToken."""
    return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")


def try_decrypt(token: str) -> str | None:
    """복호화 실패를 예외 대신 None으로 반환(로그/헬스체크용)."""
    try:
        return decrypt(token)
    except (InvalidToken, ValueError, Exception):  # noqa: BLE001
        return None


def reset_for_tests() -> None:
    """테스트에서 마스터 키/캐시를 초기화."""
    global _fernet
    with _lock:
        _fernet = None
