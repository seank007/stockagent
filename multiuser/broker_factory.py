"""사용자별 브로커 생성.

거래 엔진이 특정 사용자의 계좌에 접근할 때, 그 사용자의 복호화된 키로만
UpbitBroker를 만든다. 전역 config 키(단일 봇)와 절대 섞이지 않는다.
"""
from __future__ import annotations

from brokers.upbit import UpbitBroker

from . import accounts


def broker_for_user(user_id: int, exchange: str = "upbit", label: str = "default") -> UpbitBroker:
    """사용자의 등록된 키로 UpbitBroker 생성. 키가 없으면 LookupError."""
    cred = accounts.get_decrypted_credential(user_id, exchange=exchange, label=label)
    if cred is None:
        raise LookupError(f"user {user_id} 에 등록된 {exchange}:{label} 키가 없습니다.")
    return UpbitBroker(access_key=cred["access_key"], secret_key=cred["secret_key"])
