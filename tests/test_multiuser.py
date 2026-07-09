"""멀티유저 토대 테스트.

네트워크(업비트) 호출은 monkeypatch로 대체해서, 출금 권한 키 거부 정책과
암호화/인증/세션 로직만 검증한다. 각 테스트는 임시 DB/마스터키로 격리된다.
"""
import importlib
import os

import pytest


@pytest.fixture()
def mu(tmp_path, monkeypatch):
    """임시 DB + 임시 마스터키로 multiuser 패키지를 새로 로드."""
    monkeypatch.setenv("MULTIUSER_DB_PATH", str(tmp_path / "mu.db"))
    monkeypatch.setenv("MULTIUSER_MASTER_KEY_FILE", str(tmp_path / "master.key"))
    monkeypatch.delenv("MULTIUSER_MASTER_KEY", raising=False)

    from multiuser import db, vault, accounts, exchange
    importlib.reload(db)
    importlib.reload(vault)
    importlib.reload(exchange)
    importlib.reload(accounts)
    db.reset_for_tests()
    vault.reset_for_tests()
    return accounts, vault, exchange


# ------------------------------------------------ vault
def test_vault_roundtrip(mu):
    _, vault, _ = mu
    secret = "super-secret-upbit-key-🔐"
    token = vault.encrypt(secret)
    assert token != secret
    assert secret not in token
    assert vault.decrypt(token) == secret


def test_vault_wrong_key_fails(mu):
    _, vault, _ = mu
    token = vault.encrypt("abc")
    vault._fernet = None  # 강제로 다른 키 로드하도록
    os.environ["MULTIUSER_MASTER_KEY"] = vault.generate_master_key()
    try:
        assert vault.try_decrypt(token) is None
    finally:
        del os.environ["MULTIUSER_MASTER_KEY"]


# ------------------------------------------------ accounts / auth
def test_register_and_authenticate(mu):
    accounts, _, _ = mu
    user = accounts.register("A@Example.com ", "hunter2password")
    assert user["email"] == "a@example.com"
    assert accounts.authenticate("a@example.com", "hunter2password")["id"] == user["id"]
    assert accounts.authenticate("a@example.com", "wrong") is None
    assert accounts.authenticate("nobody@x.com", "whatever") is None


def test_duplicate_email_rejected(mu):
    accounts, _, _ = mu
    accounts.register("dup@x.com", "password123")
    with pytest.raises(accounts.AccountError):
        accounts.register("dup@x.com", "password123")


def test_weak_password_and_bad_email(mu):
    accounts, _, _ = mu
    with pytest.raises(accounts.AccountError):
        accounts.register("ok@x.com", "short")
    with pytest.raises(accounts.AccountError):
        accounts.register("not-an-email", "password123")


def test_password_not_stored_plaintext(mu):
    accounts, _, _ = mu
    from multiuser import db
    accounts.register("hash@x.com", "password123")
    row = db.connection().execute("SELECT password_hash FROM users WHERE email='hash@x.com'").fetchone()
    assert "password123" not in row["password_hash"]


def test_sessions(mu):
    accounts, _, _ = mu
    user = accounts.register("s@x.com", "password123")
    token = accounts.create_session(user["id"])
    assert accounts.user_for_session(token)["id"] == user["id"]
    accounts.revoke_session(token)
    assert accounts.user_for_session(token) is None
    assert accounts.user_for_session("garbage") is None


# ------------------------------------------------ 거래소 키 정책 (핵심)
def test_withdrawal_key_is_rejected(mu, monkeypatch):
    accounts, _, exchange = mu
    user = accounts.register("w@x.com", "password123")

    # 출금 권한이 있는 것으로 판정되는 키
    monkeypatch.setattr(
        accounts, "verify_upbit_key",
        lambda a, s: exchange.VerifyResult(valid=True, can_withdraw=True, detail="출금 권한 있음"),
    )
    with pytest.raises(accounts.AccountError):
        accounts.add_exchange_credential(user["id"], "ak", "sk")
    assert accounts.list_credentials(user["id"]) == []


def test_invalid_key_is_rejected(mu, monkeypatch):
    accounts, _, exchange = mu
    user = accounts.register("i@x.com", "password123")
    monkeypatch.setattr(
        accounts, "verify_upbit_key",
        lambda a, s: exchange.VerifyResult(valid=False, can_withdraw=False, detail="유효하지 않음"),
    )
    with pytest.raises(accounts.AccountError):
        accounts.add_exchange_credential(user["id"], "ak", "sk")


def test_valid_no_withdrawal_key_is_stored_encrypted(mu, monkeypatch):
    accounts, vault, exchange = mu
    from multiuser import db
    user = accounts.register("v@x.com", "password123")
    monkeypatch.setattr(
        accounts, "verify_upbit_key",
        lambda a, s: exchange.VerifyResult(valid=True, can_withdraw=False, detail="ok"),
    )
    accounts.add_exchange_credential(user["id"], "my-access-key", "my-secret-key")

    creds = accounts.list_credentials(user["id"])
    assert len(creds) == 1 and creds[0]["permission_verified"] is True
    # 시크릿이 평문으로 저장되지 않았는지 확인
    row = db.connection().execute(
        "SELECT access_key_enc, secret_key_enc FROM exchange_credentials WHERE user_id=?",
        (user["id"],),
    ).fetchone()
    assert "my-secret-key" not in row["secret_key_enc"]
    assert "my-access-key" not in row["access_key_enc"]
    # 거래 엔진은 복호화해서 원키를 얻는다
    dec = accounts.get_decrypted_credential(user["id"])
    assert dec == {"access_key": "my-access-key", "secret_key": "my-secret-key"}


def test_user_isolation(mu, monkeypatch):
    accounts, _, exchange = mu
    monkeypatch.setattr(
        accounts, "verify_upbit_key",
        lambda a, s: exchange.VerifyResult(valid=True, can_withdraw=False, detail="ok"),
    )
    u1 = accounts.register("u1@x.com", "password123")
    u2 = accounts.register("u2@x.com", "password123")
    accounts.add_exchange_credential(u1["id"], "u1-ak", "u1-sk")
    assert accounts.list_credentials(u2["id"]) == []
    assert accounts.get_decrypted_credential(u2["id"]) is None
    assert accounts.get_decrypted_credential(u1["id"])["access_key"] == "u1-ak"


# ------------------------------------------------ 업비트 검증 로직(HTTP 모킹)
def test_verify_upbit_key_flags_withdrawal(mu, monkeypatch):
    _, _, exchange = mu
    calls = {}

    def fake_request(path, ak, sk, params=None):
        calls[path] = True
        if path == "/v1/accounts":
            return 200, [{"currency": "KRW"}, {"currency": "BTC"}]
        if path == "/v1/withdraws/chance":
            return 200, {"currency": {"code": "KRW"}}  # 출금 조회 성공 = 권한 있음
        return 404, {}

    monkeypatch.setattr(exchange, "_request", fake_request)
    res = exchange.verify_upbit_key("ak", "sk")
    assert res.valid and res.can_withdraw and not res.acceptable


def test_verify_upbit_key_accepts_no_withdrawal(mu, monkeypatch):
    _, _, exchange = mu

    def fake_request(path, ak, sk, params=None):
        if path == "/v1/accounts":
            return 200, [{"currency": "KRW"}]
        if path == "/v1/withdraws/chance":
            return 401, {"error": {"name": "out_of_scope", "message": "no permission"}}
        return 404, {}

    monkeypatch.setattr(exchange, "_request", fake_request)
    res = exchange.verify_upbit_key("ak", "sk")
    assert res.valid and not res.can_withdraw and res.acceptable


def test_verify_upbit_key_invalid_key(mu, monkeypatch):
    _, _, exchange = mu
    monkeypatch.setattr(
        exchange, "_request",
        lambda path, ak, sk, params=None: (401, {"error": {"message": "invalid"}}),
    )
    res = exchange.verify_upbit_key("ak", "sk")
    assert not res.valid and not res.acceptable


# ------------------------------------------------ 내 계정 설정
def test_change_password(mu):
    accounts, _, _ = mu
    u = accounts.register("cp@x.com", "password123")
    token = accounts.create_session(u["id"])
    with pytest.raises(accounts.AccountError):
        accounts.change_password(u["id"], "wrong", "newpassword1")
    accounts.change_password(u["id"], "password123", "newpassword1")
    assert accounts.authenticate("cp@x.com", "newpassword1") is not None
    assert accounts.authenticate("cp@x.com", "password123") is None
    # 비번 변경 시 기존 세션은 폐기됨
    assert accounts.user_for_session(token) is None


def test_change_password_too_short(mu):
    accounts, _, _ = mu
    u = accounts.register("cp2@x.com", "password123")
    with pytest.raises(accounts.AccountError):
        accounts.change_password(u["id"], "password123", "short")


def test_update_profile(mu):
    accounts, _, _ = mu
    u = accounts.register("pr@x.com", "password123")
    out = accounts.update_profile(u["id"], "새이름")
    assert out["display_name"] == "새이름"


def test_delete_account(mu, monkeypatch):
    accounts, _, exchange = mu
    monkeypatch.setattr(accounts, "verify_upbit_key",
                        lambda a, s: exchange.VerifyResult(True, False, "ok"))
    u = accounts.register("del@x.com", "password123")
    accounts.add_exchange_credential(u["id"], "ak", "sk")
    with pytest.raises(accounts.AccountError):
        accounts.delete_account(u["id"], "wrong")
    accounts.delete_account(u["id"], "password123")
    assert accounts.get_user(u["id"]) is None
    # 키도 CASCADE로 사라짐
    assert accounts.get_decrypted_credential(u["id"]) is None


# ------------------------------------------------ 운영자(admin)
def test_admin_email_grants_admin(mu):
    accounts, _, _ = mu
    accounts._ADMIN_EMAILS = {"boss@x.com"}
    boss = accounts.register("boss@x.com", "password123")
    normal = accounts.register("joe@x.com", "password123")
    assert boss["is_admin"] is True
    assert normal["is_admin"] is False


def test_admin_list_and_actions(mu):
    accounts, _, _ = mu
    a = accounts.register("a@x.com", "password123")
    b = accounts.register("b@x.com", "password123")
    users = accounts.list_all_users()
    assert {u["email"] for u in users} == {"a@x.com", "b@x.com"}
    assert all("password" not in str(u) for u in users)  # 비번 노출 없음

    accounts.set_user_active(b["id"], False)
    assert accounts.authenticate("b@x.com", "password123") is None  # 비활성 로그인 불가

    accounts.admin_delete_user(a["id"])
    assert accounts.get_user(a["id"]) is None
