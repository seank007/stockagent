"""멀티유저 백그라운드 자동매매 데몬.

user_settings에서 auto_enabled가 1인 활성 사용자 목록을 가져와
주기적으로 각각의 계정에서 독립적인 매매 사이클을 실행한다.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from multiuser import db, trading

logger = logging.getLogger("multiuser_daemon")

_multiuser_thread: threading.Thread | None = None
_stop_event = threading.Event()

def run_for_active_users():
    """auto_enabled=1인 유저들을 가져와서 1회씩 매매를 수행한다."""
    conn = db.connection()
    with db.lock():
        rows = conn.execute("SELECT user_id FROM user_settings WHERE auto_enabled = 1").fetchall()
    
    user_ids = [row["user_id"] for row in rows]
    if not user_ids:
        return

    # 다수의 유저가 동시에 매매를 돌리면 업비트 API Rate Limit(초당 10회 등)에 걸릴 수 있으므로
    # 워커 수를 1로 고정하여 순차적으로 천천히 처리한다.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mu_worker") as executor:
        futures = {executor.submit(trading.run_once_for_user, uid): uid for uid in user_ids}
        for future in as_completed(futures):
            uid = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error("Error running cycle for user %s: %s", uid, e)


def daemon_loop():
    """주기적으로 멀티유저 매매를 실행하는 백그라운드 루프."""
    interval = max(1, int(config.INTERVAL_SECONDS))
    logger.info("Multiuser trading daemon started (interval: %ss)", interval)

    next_deadline = time.monotonic()
    while not _stop_event.is_set():
        remaining = next_deadline - time.monotonic()
        if remaining > 0:
            _stop_event.wait(min(remaining, 0.25))
            continue
        
        try:
            run_for_active_users()
        except Exception as e:
            logger.error("Multiuser daemon iteration error: %s", e)
            
        next_deadline = time.monotonic() + interval


def start_daemon() -> threading.Thread | None:
    """멀티유저 데몬을 시작한다. (production/dev entrypoint에서 호출)"""
    global _multiuser_thread
    if not config.RUN_TRADING_LOOP:
        return None
    
    if _multiuser_thread and _multiuser_thread.is_alive():
        return _multiuser_thread
        
    _stop_event.clear()
    _multiuser_thread = threading.Thread(
        target=daemon_loop,
        daemon=True,
        name="stockagent-multiuser-daemon",
    )
    _multiuser_thread.start()
    return _multiuser_thread


def stop_daemon():
    """데몬 종료 신호를 보낸다."""
    _stop_event.set()
    if _multiuser_thread:
        _multiuser_thread.join(timeout=2.0)
