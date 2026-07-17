"""거래소 API 키 검증.

핵심 안전장치: 사용자가 등록하는 업비트 키가
  (1) 유효한지(자산조회 권한으로 실제 계정 조회 성공),
  (2) **출금 권한이 없는지**
를 확인한다. 출금 권한이 있는 키는 자금 탈취 위험이 크므로 저장을 거부한다.

검증 방식(업비트 REST + JWT 서명, 외부 의존성은 PyJWT/urllib만 사용):
- GET /v1/accounts          → 200이면 키 유효(자산조회 가능)
- GET /v1/withdraws/chance  → 200이면 출금 권한 보유(=거부 대상)
                              명시적 out_of_scope 응답만 권한 없음으로 인정

fail-closed: 출금 가능 여부를 '확실히 불가'로 판정한 경우에만 can_withdraw=False.
네트워크/모호한 오류는 안전하게 보수적으로 다룬다(호출부에서 valid+not-withdraw 둘 다 요구).
"""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import jwt

_API = "https://api.upbit.com"
_TIMEOUT = 10


@dataclass
class VerifyResult:
    valid: bool                 # 키가 유효하고 자산조회 가능한가
    can_withdraw: bool          # 출금 권한을 가졌는가(True면 거부)
    detail: str                 # 사람이 읽을 설명
    account_currencies: list[str] | None = None

    @property
    def acceptable(self) -> bool:
        """저장해도 되는 키인가: 유효 + 출금권한 없음."""
        return self.valid and not self.can_withdraw


def _auth_header(access_key: str, secret_key: str, params: dict | None = None) -> dict:
    payload = {"access_key": access_key, "nonce": str(uuid.uuid4())}
    if params:
        query = urlencode(params)
        digest = hashlib.sha512(query.encode("utf-8")).hexdigest()
        payload["query_hash"] = digest
        payload["query_hash_alg"] = "SHA512"
    token = jwt.encode(payload, secret_key, algorithm="HS256")
    if isinstance(token, bytes):  # PyJWT<2 호환
        token = token.decode("ascii")
    return {"Authorization": f"Bearer {token}"}


def _request(path: str, access_key: str, secret_key: str, params: dict | None = None):
    """(status, body) 반환. 예외는 (status, {error}) 형태로 흡수."""
    url = _API + path
    if params:
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers=_auth_header(access_key, secret_key, params))
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"error": {"message": raw[:200]}}
        return e.code, body
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, {"error": {"name": "network_error", "message": str(e)}}


def verify_upbit_key(access_key: str, secret_key: str) -> VerifyResult:
    access_key = (access_key or "").strip()
    secret_key = (secret_key or "").strip()
    if not access_key or not secret_key:
        return VerifyResult(False, False, "access/secret 키가 비어 있습니다")

    # 1) 유효성 + 자산조회 권한
    status, body = _request("/v1/accounts", access_key, secret_key)
    if status == 0:
        return VerifyResult(False, False, f"업비트 연결 실패: {_msg(body)}")
    if status != 200 or not isinstance(body, list):
        return VerifyResult(False, False, f"키가 유효하지 않거나 자산조회 권한이 없습니다: {_msg(body)}")
    currencies = [str(row.get("currency")) for row in body if isinstance(row, dict)]

    # 2) 출금 권한 확인 — 있으면 거부
    w_status, w_body = _request(
        "/v1/withdraws/chance", access_key, secret_key, {"currency": "KRW"}
    )
    if w_status == 200:
        return VerifyResult(
            True, True,
            "이 키에는 출금 권한이 있습니다. 보안상 출금 권한 없는 키만 등록할 수 있어요.",
            currencies,
        )
    error_name = ""
    if isinstance(w_body, dict) and isinstance(w_body.get("error"), dict):
        error_name = str(w_body["error"].get("name") or "").strip().lower()
    if w_status in {401, 403} and error_name == "out_of_scope":
        return VerifyResult(True, False, "유효한 키(출금 권한 없음) — 등록 가능", currencies)

    # 네트워크, rate limit, 서버 오류, 알 수 없는 4xx를 권한 없음으로 오인하지 않는다.
    # 출금 불가가 명시적으로 확인되지 않았으므로 fail-closed로 등록을 거부한다.
    if w_status == 0:
        detail = "업비트 연결 실패"
    else:
        detail = f"업비트 응답 HTTP {w_status}"
    return VerifyResult(
        True, True,
        f"출금 권한 확인에 실패했습니다({detail}). 안전을 위해 등록을 보류합니다: {_msg(w_body)}",
        currencies,
    )


def _msg(body) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("message") or err.get("name") or str(err)
        return str(body)[:200]
    return str(body)[:200]
