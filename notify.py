"""텔레그램 알림 (best-effort).

체결·봇 정지/재개·에러 같은 이벤트를 텔레그램으로 보낸다. 설정이 없으면
조용히 무시하고, 전송 실패가 매매 흐름을 막지 않도록 예외를 삼킨다.

.env:
  TELEGRAM_BOT_TOKEN=123456:ABC...
  TELEGRAM_CHAT_ID=123456789
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

import config


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _post(text: str) -> None:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:  # noqa: BLE001 - 알림 실패는 매매를 막지 않는다
        pass


def send(text: str) -> None:
    """비동기로 텔레그램 메시지를 보낸다(설정 없으면 무시)."""
    if not enabled():
        return
    threading.Thread(target=_post, args=(text,), daemon=True).start()


def send_order(*, side: str, ticker: str, krw_amount: float, volume: float,
               price: float, dry_run: bool, source: str = "manual") -> None:
    tag = "🧪 모의" if dry_run else "🔴 실거래"
    arrow = "🟢 매수" if side == "buy" else "🔻 매도"
    send(
        f"{tag} · {arrow} <b>{ticker}</b> ({source})\n"
        f"체결가 {price:,.0f}원 · 수량 {volume:.8f}\n"
        f"금액 ≈ {krw_amount:,.0f}원"
    )
