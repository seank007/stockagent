"""한국투자증권(KIS) Open API 브로커 + 페이퍼(가상) 브로커.

.env 설정:
  KIS_APP_KEY / KIS_APP_SECRET : KIS Developers에서 발급
  KIS_ACCOUNT_NO               : 계좌번호 "12345678-01" 형식 (종합계좌 8자리-상품 2자리)
  KIS_PAPER                    : true면 모의투자 도메인 사용 (기본 true)

키가 없으면 get_stock_broker()가 PaperStockBroker(가상 예수금, 네이버 실시세,
즉시 체결 시뮬레이션)를 반환하므로 파이프라인 전체가 키 없이도 동작한다.

토큰은 발급 제한(분당 1회)이 있어 ~/.stockagent_kis_token.json 에 캐시한다.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import stock_db

try:
    import certifi
except Exception:  # noqa: BLE001
    certifi = None

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 stockagent/1.0"}
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where()) if certifi else None

REAL_BASE = "https://openapi.koreainvestment.com:9443"
PAPER_BASE = "https://openapivts.koreainvestment.com:29443"
REAL_WEBSOCKET_URL = "ws://ops.koreainvestment.com:21000/tryitout"
PAPER_WEBSOCKET_URL = "ws://ops.koreainvestment.com:31000/tryitout"
TOKEN_CACHE = Path.home() / ".stockagent_kis_token.json"


def market_status(now: datetime | None = None) -> dict:
    """국내 주식 정규장 여부 (평일 09:00~15:30 KST). 공휴일은 미반영."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    weekday_ok = now.weekday() < 5
    t = now.hour * 60 + now.minute
    hours_ok = 9 * 60 <= t < 15 * 60 + 30
    open_ = weekday_ok and hours_ok
    return {
        "open": open_,
        "now": now.strftime("%Y-%m-%d %H:%M"),
        "note": "정규장" if open_ else ("주말 휴장" if not weekday_ok else "장외 시간(09:00~15:30 KST)"),
    }


def _http_json(url: str, headers: dict | None = None, data: dict | None = None,
               timeout: int = 10) -> dict:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers={**UA, **(headers or {})})
    if body is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    kwargs = {"timeout": timeout}
    if SSL_CONTEXT and urllib.parse.urlparse(url).scheme == "https":
        kwargs["context"] = SSL_CONTEXT
    with urllib.request.urlopen(req, **kwargs) as res:
        return json.loads(res.read().decode("utf-8"))


# ---------- 네이버 시세 (페이퍼 모드·보조용, 서버측 호출) ----------
def naver_quote(code: str) -> dict:
    """현재가·종목명·전일대비. 장외에는 마지막 체결가."""
    data = _http_json(f"https://m.stock.naver.com/api/stock/{code}/basic")
    price = float(str(data.get("closePrice") or "0").replace(",", ""))
    rate = data.get("fluctuationsRatio")
    return {
        "code": code,
        "name": data.get("stockName") or code,
        "price": price,
        "change_pct": float(rate) if rate not in (None, "") else 0.0,
    }


def naver_closes(code: str, count: int = 40) -> list[float]:
    """일봉 종가 시계열 (RSI/이평 계산용)."""
    import xml.etree.ElementTree as ET
    url = (f"https://fchart.stock.naver.com/sise.nhn?symbol={code}"
           f"&timeframe=day&requestType=0&count={count}")
    req = urllib.request.Request(url, headers=UA)
    kwargs = {"timeout": 10}
    if SSL_CONTEXT:
        kwargs["context"] = SSL_CONTEXT
    with urllib.request.urlopen(req, **kwargs) as res:
        xml_text = res.read().decode("euc-kr", errors="ignore")
    closes = []
    for item in ET.fromstring(xml_text).findall(".//item"):
        parts = (item.attrib.get("data") or "").split("|")
        if len(parts) >= 5:
            closes.append(float(parts[4]))
    return closes


# ---------- KIS 실계좌/모의투자 ----------
class KISBroker:
    """한국투자증권 REST. paper=True면 모의투자 도메인·TR ID 사용."""

    def __init__(self) -> None:
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        account = os.getenv("KIS_ACCOUNT_NO", "").strip()
        self.cano, _, self.prdt = account.partition("-")
        self.prdt = self.prdt or "01"
        self.paper = (os.getenv("KIS_PAPER", "true").strip().lower()
                      in {"1", "true", "yes", "y", "on"})
        self.base = PAPER_BASE if self.paper else REAL_BASE
        self.websocket_url = PAPER_WEBSOCKET_URL if self.paper else REAL_WEBSOCKET_URL
        self.is_paper_broker = False  # 로컬 가상 브로커 아님 (KIS 모의투자는 실API)
        self._approval_key = ""
        self._approval_key_expires_at = 0.0

    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret and len(self.cano) == 8)

    def is_realtime_configured(self) -> bool:
        """Realtime quotes need app credentials but not an account number."""
        return bool(self.app_key and self.app_secret)

    def websocket_approval_key(self) -> str:
        """Issue and briefly cache the KIS WebSocket approval key."""
        if not self.is_realtime_configured():
            raise RuntimeError("KIS 실시간 시세용 APP KEY/SECRET이 설정되지 않았습니다")
        if self._approval_key and time.time() < self._approval_key_expires_at:
            return self._approval_key

        res = _http_json(self.base + "/oauth2/Approval", data={
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        })
        approval_key = str(res.get("approval_key") or "").strip()
        if not approval_key:
            raise RuntimeError("KIS 웹소켓 접속키 발급 응답에 approval_key가 없습니다")
        self._approval_key = approval_key
        self._approval_key_expires_at = time.time() + 23 * 60 * 60
        return approval_key

    # --- 토큰 ---
    def _token(self) -> str:
        cache_key = "paper" if self.paper else "real"
        try:
            cached = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            entry = cached.get(cache_key) or {}
            if entry.get("token") and time.time() < float(entry.get("expires_at", 0)) - 600:
                return entry["token"]
        except Exception:  # noqa: BLE001
            cached = {}
        res = _http_json(self.base + "/oauth2/tokenP", data={
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        })
        token = res["access_token"]
        cached[cache_key] = {"token": token,
                             "expires_at": time.time() + float(res.get("expires_in", 86400))}
        TOKEN_CACHE.write_text(json.dumps(cached), encoding="utf-8")
        return token

    def _headers(self, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {self._token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        url = self.base + path + "?" + urllib.parse.urlencode(params)
        return _http_json(url, headers=self._headers(tr_id))

    def _post(self, path: str, tr_id: str, data: dict) -> dict:
        return _http_json(self.base + path, headers=self._headers(tr_id), data=data)

    # --- 시세 ---
    def quote(self, code: str) -> dict:
        res = self._get("/uapi/domestic-stock/v1/quotations/inquire-price",
                        "FHKST01010100",
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
        out = res.get("output") or {}
        return {
            "code": code,
            "name": out.get("bstp_kor_isnm") or code,
            "price": float(out.get("stck_prpr") or 0),
            "change_pct": float(out.get("prdy_ctrt") or 0),
        }

    # --- 잔고 ---
    def balance(self) -> dict:
        tr = "VTTC8434R" if self.paper else "TTTC8434R"
        res = self._get("/uapi/domestic-stock/v1/trading/inquire-balance", tr, {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
            "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        })
        holdings = []
        for row in res.get("output1") or []:
            qty = int(float(row.get("hldg_qty") or 0))
            if qty <= 0:
                continue
            holdings.append({
                "code": row.get("pdno"),
                "name": row.get("prdt_name"),
                "qty": qty,
                "avg_price": float(row.get("pchs_avg_pric") or 0),
                "current_price": float(row.get("prpr") or 0),
                "eval_amount": float(row.get("evlu_amt") or 0),
                "pnl": float(row.get("evlu_pfls_amt") or 0),
                "return_pct": float(row.get("evlu_pfls_rt") or 0),
            })
        out2 = (res.get("output2") or [{}])[0]
        return {
            "cash": float(out2.get("dnca_tot_amt") or 0),
            "total_eval": float(out2.get("tot_evlu_amt") or 0),
            "holdings": holdings,
            "paper": self.paper,
            "source": "KIS 모의투자" if self.paper else "KIS 실계좌",
        }

    # --- 주문 (시장가) ---
    def _order(self, code: str, qty: int, side: str) -> dict:
        if side == "buy":
            tr = "VTTC0802U" if self.paper else "TTTC0802U"
        else:
            tr = "VTTC0801U" if self.paper else "TTTC0801U"
        res = self._post("/uapi/domestic-stock/v1/trading/order-cash", tr, {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt,
            "PDNO": code, "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(qty), "ORD_UNPR": "0",
        })
        if str(res.get("rt_cd")) != "0":
            raise RuntimeError(f"KIS 주문 거부: {res.get('msg1')} ({res.get('msg_cd')})")
        return res

    def market_buy(self, code: str, qty: int) -> dict:
        q = self.quote(code)
        res = self._order(code, qty, "buy")
        rec = stock_db.record_trade(code, q["name"], "buy", q["price"], qty,
                                    paper=False, raw_result=json.dumps(res, ensure_ascii=False))
        return {**rec, "kis": res.get("output"), "mode": "KIS " + ("모의" if self.paper else "실전")}

    def market_sell(self, code: str, qty: int) -> dict:
        q = self.quote(code)
        res = self._order(code, qty, "sell")
        rec = stock_db.record_trade(code, q["name"], "sell", q["price"], qty,
                                    paper=False, raw_result=json.dumps(res, ensure_ascii=False))
        return {**rec, "kis": res.get("output"), "mode": "KIS " + ("모의" if self.paper else "실전")}


# ---------- 페이퍼 브로커 (키 발급 전 가상 체결) ----------
class PaperStockBroker:
    """가상 예수금 + 네이버 실시세 + 즉시 체결 시뮬레이션."""

    is_paper_broker = True
    paper = True

    def is_configured(self) -> bool:
        return True

    def quote(self, code: str) -> dict:
        return naver_quote(code)

    def balance(self) -> dict:
        cash = stock_db.get_cash()
        holdings = []
        for p in stock_db.positions():
            try:
                q = naver_quote(p["code"])
                cur = q["price"]
            except Exception:  # noqa: BLE001
                cur = p["avg_price"]
            eval_amt = cur * p["qty"]
            holdings.append({
                "code": p["code"], "name": p["name"], "qty": p["qty"],
                "avg_price": p["avg_price"], "current_price": cur,
                "eval_amount": eval_amt,
                "pnl": eval_amt - p["avg_price"] * p["qty"],
                "return_pct": (cur / p["avg_price"] - 1) * 100 if p["avg_price"] > 0 else 0,
            })
        return {
            "cash": cash,
            "total_eval": cash + sum(h["eval_amount"] for h in holdings),
            "holdings": holdings,
            "paper": True,
            "source": "페이퍼(가상 체결·실시세)",
        }

    def market_buy(self, code: str, qty: int) -> dict:
        q = naver_quote(code)
        if q["price"] <= 0:
            raise RuntimeError(f"{code} 시세 조회 실패")
        cost = q["price"] * qty * (1 + stock_db.COMMISSION_RATE)
        if cost > stock_db.get_cash():
            raise RuntimeError(f"가상 예수금 부족: 필요 {cost:,.0f}원 > 보유 {stock_db.get_cash():,.0f}원")
        rec = stock_db.record_trade(code, q["name"], "buy", q["price"], qty,
                                    paper=True, raw_result='{"paper": true}')
        return {**rec, "mode": "페이퍼"}

    def market_sell(self, code: str, qty: int) -> dict:
        pos = {p["code"]: p for p in stock_db.positions()}.get(code)
        if not pos or pos["qty"] < qty:
            raise RuntimeError(f"{code} 보유 수량 부족 (보유 {pos['qty'] if pos else 0}주)")
        q = naver_quote(code)
        rec = stock_db.record_trade(code, q["name"], "sell", q["price"], qty,
                                    paper=True, raw_result='{"paper": true}')
        return {**rec, "mode": "페이퍼"}


def get_stock_broker():
    """KIS 키가 설정돼 있으면 KIS, 아니면 페이퍼 브로커."""
    kis = KISBroker()
    return kis if kis.is_configured() else PaperStockBroker()
