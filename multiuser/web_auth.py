"""Flask 인증/키관리 라우트.

web.py에서 한 줄로 얹는다:
    import multiuser.web_auth as web_auth
    web_auth.register(app)

세션은 httpOnly 쿠키(sa_session)에 세션 토큰을 담아 관리한다.
보호가 필요한 라우트에는 @login_required, 사용자 식별은 current_user()를 쓴다.
"""
from __future__ import annotations

import functools
import os

from flask import Blueprint, current_app, g, jsonify, make_response, request

from . import accounts

bp = Blueprint("multiuser_auth", __name__)

_COOKIE = "sa_session"
_COOKIE_MAX_AGE = accounts.SESSION_TTL_HOURS * 3600
# 운영(HTTPS)에서는 반드시 secure 쿠키. 로컬 http 개발에선 끌 수 있게 env로.
_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes", "on"}


def current_user() -> dict | None:
    """요청당 1회 세션 검증 후 캐시."""
    if "mu_user" not in g:
        token = request.cookies.get(_COOKIE, "")
        g.mu_user = accounts.user_for_session(token)
    return g.mu_user


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return jsonify({"error": "로그인이 필요합니다."}), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"error": "로그인이 필요합니다."}), 401
        if not user.get("is_admin"):
            return jsonify({"error": "관리자 권한이 필요합니다."}), 403
        return view(*args, **kwargs)

    return wrapped


def _set_session_cookie(resp, token: str):
    resp.set_cookie(
        _COOKIE, token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )
    return resp


@bp.post("/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    try:
        user = accounts.register(
            data.get("email", ""), data.get("password", ""), data.get("display_name"),
        )
    except accounts.AccountError as e:
        return jsonify({"error": str(e)}), 400
    token = accounts.create_session(user["id"])
    return _set_session_cookie(make_response(jsonify({"user": user}), 201), token)


@bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    user = accounts.authenticate(data.get("email", ""), data.get("password", ""))
    if user is None:
        return jsonify({"error": "이메일 또는 비밀번호가 올바르지 않습니다."}), 401
    token = accounts.create_session(user["id"])
    return _set_session_cookie(make_response(jsonify({"user": user})), token)


@bp.post("/auth/logout")
def logout():
    token = request.cookies.get(_COOKIE, "")
    if token:
        accounts.revoke_session(token)
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(_COOKIE, path="/")
    return resp


@bp.get("/auth/me")
def me():
    user = current_user()
    if user is None:
        return jsonify({"user": None}), 200
    return jsonify({"user": user})


# ------------------------------------------------ 거래소 키 관리
@bp.get("/auth/credentials")
@login_required
def list_creds():
    return jsonify({"credentials": accounts.list_credentials(current_user()["id"])})


@bp.post("/auth/credentials")
@login_required
def add_cred():
    data = request.get_json(silent=True) or {}
    try:
        saved = accounts.add_exchange_credential(
            current_user()["id"],
            data.get("access_key", ""),
            data.get("secret_key", ""),
            exchange=(data.get("exchange") or "upbit"),
            label=(data.get("label") or "default"),
        )
    except accounts.AccountError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"credential": saved}), 201


@bp.delete("/auth/credentials")
@login_required
def delete_cred():
    data = request.get_json(silent=True) or {}
    ok = accounts.delete_credential(
        current_user()["id"],
        exchange=(data.get("exchange") or "upbit"),
        label=(data.get("label") or "default"),
    )
    return jsonify({"ok": ok}), (200 if ok else 404)


# ------------------------------------------------ 내 자산 조회 (읽기 전용)
@bp.get("/auth/portfolio")
@login_required
def portfolio():
    from . import broker_factory

    uid = current_user()["id"]
    try:
        broker = broker_factory.broker_for_user(uid)
    except LookupError:
        return jsonify({"error": "등록된 거래소 키가 없습니다.", "need_key": True}), 400
    try:
        pf = broker.get_portfolio()
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"자산 조회에 실패했습니다: {e}"}), 502
    return jsonify({"portfolio": pf})


# ------------------------------------------------ 자동매매(베타)
@bp.get("/auth/settings")
@login_required
def get_settings():
    from . import trading

    return jsonify({"settings": trading.get_settings(current_user()["id"])})


@bp.post("/auth/settings")
@login_required
def post_settings():
    from . import trading

    data = request.get_json(silent=True) or {}
    s = trading.update_settings(
        current_user()["id"],
        auto_enabled=data.get("auto_enabled"),
        dry_run=data.get("dry_run"),
        tickers=data.get("tickers"),
        max_order_krw=data.get("max_order_krw"),
    )
    return jsonify({"settings": s})


@bp.post("/auth/trade/run_once")
@login_required
def run_once():
    """수동 1회 실행. 안전을 위해 항상 모의(dry_run)로만 돈다."""
    from . import trading

    uid = current_user()["id"]
    try:
        result = trading.run_once_for_user(uid, dry_run=True)  # 수동은 강제 모의
    except LookupError:
        return jsonify({"error": "등록된 거래소 키가 없습니다.", "need_key": True}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"실행 실패: {e}"}), 502
    return jsonify(result)


@bp.get("/auth/trade/history")
@login_required
def trade_history():
    from . import trading

    uid = current_user()["id"]
    return jsonify({
        "decisions": trading.recent_decisions(uid, 20),
        "trades": trading.recent_trades(uid, 20),
    })


# ------------------------------------------------ 내 계정 설정
@bp.post("/auth/account/password")
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    try:
        accounts.change_password(
            current_user()["id"], data.get("current_password", ""), data.get("new_password", "")
        )
    except accounts.AccountError as e:
        return jsonify({"error": str(e)}), 400
    resp = make_response(jsonify({"ok": True, "relogin": True}))
    resp.delete_cookie(_COOKIE, path="/")  # 비번 변경 → 재로그인
    return resp


@bp.post("/auth/account/profile")
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    user = accounts.update_profile(current_user()["id"], data.get("display_name"))
    return jsonify({"user": user})


@bp.post("/auth/account/delete")
@login_required
def delete_account():
    data = request.get_json(silent=True) or {}
    try:
        accounts.delete_account(current_user()["id"], data.get("password", ""))
    except accounts.AccountError as e:
        return jsonify({"error": str(e)}), 400
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(_COOKIE, path="/")
    return resp


# ------------------------------------------------ 운영자(admin)
@bp.get("/auth/admin/users")
@admin_required
def admin_users():
    return jsonify({"users": accounts.list_all_users()})


@bp.post("/auth/admin/users/<int:uid>")
@admin_required
def admin_user_action(uid: int):
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    me = current_user()
    if uid == me["id"] and action in ("deactivate", "delete"):
        return jsonify({"error": "본인 계정은 여기서 비활성화/삭제할 수 없습니다."}), 400
    if action == "activate":
        accounts.set_user_active(uid, True)
    elif action == "deactivate":
        accounts.set_user_active(uid, False)
    elif action == "delete":
        accounts.admin_delete_user(uid)
    else:
        return jsonify({"error": "알 수 없는 동작"}), 400
    return jsonify({"ok": True})


# ------------------------------------------------ 사용자/관리자 화면
@bp.get("/app")
def app_page():
    from .frontend import PAGE_HTML

    return current_app.response_class(PAGE_HTML, mimetype="text/html")


@bp.get("/admin")
def admin_page():
    from .frontend import ADMIN_HTML

    return current_app.response_class(ADMIN_HTML, mimetype="text/html")


def register(app) -> None:
    """web.py에서 호출. 인증 블루프린트를 앱에 등록한다."""
    app.register_blueprint(bp)
    app.logger.info("[multiuser] auth routes registered (/auth/*)")
