"""Export the real Flask-rendered UI shell for GitHub Pages.

GitHub Pages cannot run the Flask server or private Upbit/AI calls. This export
uses the same HTML/CSS/JS constants as the app, then injects a small static API
shim with safe demo data so the public page looks and behaves like the dashboard
without publishing account data or secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
os.environ.setdefault("AUTO_OPEN_BROWSER", "false")
os.environ.setdefault("RUN_TRADING_LOOP", "false")

import web  # noqa: E402

DOCS = ROOT / "docs"


def _asset_version(relative: str) -> str:
    """Return a short content hash so Pages deploys bypass stale browser caches."""
    return hashlib.sha256((DOCS / relative).read_bytes()).hexdigest()[:12]


MARKETS = [
    {"market": "KRW-BTC", "symbol": "BTC", "korean_name": "비트코인", "english_name": "Bitcoin"},
    {"market": "KRW-SOL", "symbol": "SOL", "korean_name": "솔라나", "english_name": "Solana"},
    {"market": "KRW-ETH", "symbol": "ETH", "korean_name": "이더리움", "english_name": "Ethereum"},
    {"market": "KRW-XRP", "symbol": "XRP", "korean_name": "리플", "english_name": "XRP"},
    {"market": "KRW-DOGE", "symbol": "DOGE", "korean_name": "도지코인", "english_name": "Dogecoin"},
    {"market": "KRW-ADA", "symbol": "ADA", "korean_name": "에이다", "english_name": "Cardano"},
    {"market": "KRW-SUI", "symbol": "SUI", "korean_name": "수이", "english_name": "Sui"},
    {"market": "KRW-LINK", "symbol": "LINK", "korean_name": "체인링크", "english_name": "Chainlink"},
    {"market": "KRW-AVAX", "symbol": "AVAX", "korean_name": "아발란체", "english_name": "Avalanche"},
    {"market": "KRW-DOT", "symbol": "DOT", "korean_name": "폴카닷", "english_name": "Polkadot"},
    {"market": "KRW-TRX", "symbol": "TRX", "korean_name": "트론", "english_name": "TRON"},
    {"market": "KRW-BCH", "symbol": "BCH", "korean_name": "비트코인캐시", "english_name": "Bitcoin Cash"},
]


DEMO_PORTFOLIO = {
    "data_mode": "demo",
    "generated_at": None,
    "summary": {
        "total_principal": 1_000_000,
        "total_value": 1_025_000,
        "cash_value": 400_000,
        "coin_value": 625_000,
        "cash_ratio": 39.0,
        "unrealized_pnl": 25_000,
        "realized_pnl": 0,
        "total_pnl": 25_000,
        "total_return_pct": 2.5,
        "assets_count": 3,
        "largest_asset": "KRW",
        "largest_weight": 39.0,
    },
    "holdings": [
        {
            "currency": "KRW",
            "ticker": "KRW",
            "balance": 400_000,
            "avg_buy_price": 1,
            "principal": 400_000,
            "current_price": 1,
            "current_value": 400_000,
            "return_pct": 0,
            "weight": 39.0,
            "unrealized_pnl": 0,
            "realized_pnl": 0,
            "trades_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "last_trade_at": None,
        },
        {
            "currency": "BTC",
            "ticker": "KRW-BTC",
            "balance": 0.003,
            "avg_buy_price": 100_000_000,
            "principal": 300_000,
            "current_price": 105_000_000,
            "current_value": 315_000,
            "return_pct": 5.0,
            "weight": 30.7,
            "unrealized_pnl": 15_000,
            "realized_pnl": 0,
            "trades_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "last_trade_at": None,
        },
        {
            "currency": "ETH",
            "ticker": "KRW-ETH",
            "balance": 0.1,
            "avg_buy_price": 3_000_000,
            "principal": 300_000,
            "current_price": 3_100_000,
            "current_value": 310_000,
            "return_pct": 3.33,
            "weight": 30.3,
            "unrealized_pnl": 10_000,
            "realized_pnl": 0,
            "trades_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "last_trade_at": None,
        },
    ],
    "manual_items": [],
    "account_error": None,
    "daily": [],
    "risk": {"max_order_krw": 10_000, "max_daily_loss_krw": 30_000, "min_confidence": 0.6},
}

DEMO_AI_SNAPSHOT = {
    "data_mode": "demo",
    "generated_at": None,
    "state": {
        "last_update": None,
        "loop_running": False,
        "bot_paused": True,
        "history": [
            {
                "time": "DEMO",
                "ticker": "KRW-BTC",
                "price": 105_000_000,
                "rsi": 50,
                "trend": "flat",
                "change_pct": 0,
                "action": "HOLD",
                "confidence": 0,
                "reasoning": "공개 정적 데모에서는 계좌 조회, AI 실행, 주문을 수행하지 않습니다.",
                "order": "none | public demo",
            }
        ],
    },
    "config": {
        "provider": "mock",
        "model": "github-pages-static",
        "tickers": ["KRW-BTC", "KRW-ETH"],
        "dry_run": True,
        "allow_live_trading": False,
        "free_trade_mode": False,
        "external_trader": False,
        "risk": {
            "max_order_krw": 10_000,
            "min_order_krw": 5_000,
            "max_daily_loss_krw": 30_000,
            "min_confidence": 0.6,
            "cycle_seconds": 600,
        },
    },
    "capabilities": {"live_account": False, "trading": False, "control": False},
    "trades": [],
}

DEMO_STOCK_SNAPSHOT = {
    "data_mode": "demo",
    "generated_at": None,
    "market": {"open": False, "now": None, "note": "공개 정적 데모"},
    "mode": "DEMO",
    "paper": True,
    "cash": 1_000_000,
    "total_eval": 1_000_000,
    "principal": 0,
    "unrealized_pnl": 0,
    "today_realized": 0,
    "today_trades": 0,
    "total_realized": 0,
    "holdings": [],
    "daily": [],
    "decisions": [],
    "trades": [],
    "watchlist": [],
    "analysis": {},
    "capabilities": {"live_account": False, "trading": False, "control": False},
}


STATIC_API = f"""
<script>
(() => {{
  const DEMO_PORTFOLIO = __PORTFOLIO_JSON__;
  const MARKETS = {json.dumps(MARKETS, ensure_ascii=False)};
  const PRICES = {{
    "KRW-BTC": 93005000, "KRW-ETH": 2586000, "KRW-SOL": 122000,
    "KRW-XRP": 1660, "KRW-DOGE": 113, "KRW-ADA": 249,
    "KRW-SUI": 3780, "KRW-LINK": 19800, "KRW-AVAX": 31200,
    "KRW-DOT": 5520, "KRW-TRX": 468, "KRW-BCH": 739000,
    "005930": 74200, "000660": 183500, "035420": 215000,
    "005380": 248000, "035720": 54800
  }};
  const CHANGES = {{
    "KRW-BTC": 0.06, "KRW-ETH": 0.78, "KRW-SOL": 0.16,
    "KRW-XRP": 1.09, "KRW-DOGE": 0.89, "KRW-ADA": 2.05,
    "KRW-SUI": -0.44, "KRW-LINK": 0.72, "KRW-AVAX": -0.22,
    "KRW-DOT": 0.31, "KRW-TRX": 0.18, "KRW-BCH": 0.54
  }};
  const now = () => new Date().toISOString();
  const clone = value => JSON.parse(JSON.stringify(value));
  const symbol = ticker => String(ticker || "KRW-BTC").replace("KRW-", "");
  const priceFor = ticker => PRICES[String(ticker || "KRW-BTC").toUpperCase()] || 100000;

  function movingAverage(values, period) {{
    return values.map((_, idx) => {{
      if (idx + 1 < period) return null;
      const slice = values.slice(idx + 1 - period, idx + 1);
      return slice.reduce((a, b) => a + b, 0) / period;
    }});
  }}

  function rsi(values) {{
    return values.map((_, idx) => {{
      if (idx < 14) return 50;
      let gain = 0, loss = 0;
      for (let i = idx - 13; i <= idx; i++) {{
        const diff = values[i] - values[i - 1];
        if (diff >= 0) gain += diff;
        else loss -= diff;
      }}
      if (loss === 0) return 100;
      const rs = gain / loss;
      return 100 - (100 / (1 + rs));
    }});
  }}

  function candles(ticker="KRW-BTC", interval="minute60", count=120) {{
    ticker = String(ticker || "KRW-BTC").toUpperCase();
    count = Math.max(30, Math.min(Number(count || 120), 160));
    const base = priceFor(ticker);
    const seed = [...ticker].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
    const closes = [];
    for (let i = 0; i < count; i++) {{
      const wave = Math.sin((i + seed) / 8) * 0.026 + Math.cos((i + seed) / 17) * 0.014;
      const drift = (i - count * 0.62) / count * 0.038;
      closes.push(Math.round(base * (1 + wave + drift)));
    }}
    closes[closes.length - 1] = base;
    return {{
      ticker, interval, cached: true,
      closes,
      ma5: movingAverage(closes, 5),
      ma20: movingAverage(closes, 20),
      rsi: rsi(closes)
    }};
  }}

  function orderbook(ticker) {{
    const p = priceFor(ticker);
    const units = Array.from({{length: 12}}, (_, i) => {{
      const step = Math.max(1, Math.round(p * 0.00035));
      return {{
        ask_price: p + step * (i + 1),
        ask_size: Number((0.28 + i * 0.041).toFixed(6)),
        bid_price: p - step * (i + 1),
        bid_size: Number((0.31 + i * 0.037).toFixed(6)),
      }};
    }});
    return {{
      ticker,
      timestamp: Date.now(),
      live: false,
      stale: true,
      source: "demo-snapshot",
      total_ask_size: 9.42,
      total_bid_size: 10.18,
      units,
    }};
  }}

  function state() {{
    // 공개 Pages에는 검증된 데모 스냅샷만 심는다.
    const snap = window.__aiTradeSnapshot || {{}};
    const scfg = snap.config || {{}};
    const sstate = snap.state || {{}};
    return {{
      data_mode: "demo",
      mode: "PUBLIC DEMO",
      dry_run: true,
      allow_live_trading: false,
      capabilities: {{live_account:false, trading:false, control:false}},
      provider: "mock",
      model: "github-pages-static",
      loop_running: false,
      bot_paused: true,
      started_at: "GitHub Pages",
      interval: 600,
      cycle_count: (sstate.history || []).length,
      last_update: sstate.last_update || snap.generated_at || null,
      today_pnl: 0,
      total_pnl: 0,
      error: null,
      history: (sstate.history && sstate.history.length) ? sstate.history : [
        {{time:"15:42:10", ticker:"KRW-BTC", price:priceFor("KRW-BTC"), rsi:58.2, trend:"up", change_pct:0.8, action:"HOLD", confidence:0.54, reasoning:"공개 데모 모드입니다. GitHub Pages에서는 실제 주문과 AI 호출을 실행하지 않습니다.", order:"none | demo"}},
        {{time:"15:40:05", ticker:"KRW-SOL", price:priceFor("KRW-SOL"), rsi:51.7, trend:"down", change_pct:0.2, action:"HOLD", confidence:0.48, reasoning:"모의 데이터로 대시보드 UI를 표시합니다.", order:"none | demo"}}
      ]
    }};
  }}

  function pnl() {{
    // 공개 데모 데이터만 계산한다. 실제 계좌 데이터는 정적 파일에 저장하지 않는다.
    const pf = window.__initialCoinPortfolio || DEMO_PORTFOLIO;
    const snap = window.__aiTradeSnapshot || {{}};
    const trades = snap.trades || [];
    const rows = pf.daily || [];
    const today = new Date().toLocaleDateString("sv-SE", {{ timeZone: "Asia/Seoul" }});
    const todayRow = rows.find(d => String(d.day || d.date || "") === today);
    const todayFromTrades = trades.filter(t => String(t.ts || "").slice(0, 10) === today);
    const closed = trades.filter(t => t.side === "sell" && Number(t.realized_pnl || 0) !== 0);
    const wins = closed.filter(t => Number(t.realized_pnl) > 0).length;
    const daily = rows.map(d => ({{ ...d, date: String(d.day || d.date || "").slice(5) }}));
    return {{
      today: todayRow ? Number(todayRow.realized_pnl || 0)
        : todayFromTrades.reduce((sum, t) => sum + Number(t.realized_pnl || 0), 0),
      total: Number((pf.summary || {{}}).realized_pnl || 0),
      today_trades: todayRow ? Number(todayRow.trades_count || 0) : todayFromTrades.length,
      win_rate: closed.length ? wins / closed.length * 100 : 0,
      daily,
      trades,
      as_of: pf.generated_at || snap.generated_at || null
    }};
  }}

  function manualOrderPreview(init) {{
    let payload = {{}};
    try {{ payload = JSON.parse(init?.body || "{{}}"); }} catch(e) {{}}
    const ticker = String(payload.ticker || "KRW-BTC").toUpperCase();
    const side = String(payload.side || "buy").toLowerCase();
    const price = priceFor(ticker);
    const krw = Number(payload.krw_amount || 10000);
    const volume = side === "sell" ? Number(payload.volume || 0.0001) : krw / price;
    return {{
      ok: true,
      preview: {{
        side, ticker, price, volume, krw_amount: side === "buy" ? krw : volume * price,
        estimated_fee: (side === "buy" ? krw : volume * price) * 0.0005,
        dry_run: true,
        min_order_krw: 5000,
      }}
    }};
  }}

  function news() {{
    const items = [
      ["CoinDesk", "Bitcoin holds range as traders watch macro liquidity", "시장 유동성과 위험선호 회복 여부가 단기 방향의 핵심 변수로 거론됩니다."],
      ["TokenPost", "솔라나 생태계 거래 활동 증가", "온체인 활동과 디앱 수수료 흐름이 다시 주목받고 있습니다."],
      ["Blockmedia", "가상자산 시장, 주요 알트코인 반등", "대형 코인 중심으로 변동성이 확대되는 모습입니다."],
      ["Cointelegraph", "Ethereum traders monitor ETF flows", "기관성 수급과 네트워크 지표가 함께 관찰되고 있습니다."],
    ].map((n, i) => ({{
      source: n[0], publisher: n[0], title: n[1], summary: n[2],
      link: "https://github.com/seank007/stockagent",
      published: new Date(Date.now() - (i + 1) * 900000).toISOString(),
      relative_time: `${{(i + 1) * 15}}분 전`
    }}));
    return {{
      items, total_count: items.length,
      generated_at: now(),
      sources: ["Google KR", "Google Global", "Blockmedia", "TokenPost", "CoinDesk"].map((name, idx) => ({{name, count: idx < 2 ? 2 : 1, ok: true}})),
      errors: []
    }};
  }}

  function newsSummary() {{
    return {{
      headline: "대형 코인은 혼조, 시장은 유동성과 규제 신호를 대기",
      market_mood: "혼조",
      brief: ["비트코인은 박스권에서 방향성을 탐색 중입니다.", "솔라나와 이더리움은 생태계/수급 이슈가 함께 주목됩니다.", "공개 페이지에서는 실시간 뉴스 API 대신 데모 요약을 표시합니다."],
      key_assets: ["BTC", "ETH", "SOL", "XRP"],
      risks: ["높은 변동성", "거래소/규제 뉴스", "레버리지 청산"],
      watch: ["BTC 주요 가격대", "ETF/기관 수급", "온체인 활동"],
      source_note: "GitHub Pages 정적 데모 데이터",
      generated_at: now()
    }};
  }}

  function analyze(ticker) {{
    const snap = {{...candles(ticker, "minute60", 120), price: priceFor(ticker), rsi14: 54.8, trend: "up", period_change_pct: 1.2}};
    return {{
      snapshot: snap,
      cached: true,
      report: {{
        summary: "GitHub Pages 공개 데모 분석입니다. 실제 AI 판단은 서버 배포에서 실행됩니다.",
        trend: "단기 추세는 완만한 반등으로 표시됩니다.",
        momentum: "RSI는 중립권이며 과열 신호는 제한적입니다.",
        support_resistance: "가상 지지/저항 구간을 데모로 표시합니다.",
        risks: "정적 페이지에서는 실시간 체결/계좌/API 호출이 없습니다.",
        watch: "실제 운영은 Flask 서버 배포 후 DRY_RUN 상태에서 먼저 검증하세요."
      }}
    }};
  }}

  function route(path, params, init) {{
    const readOnly = () => ({{__status:405, error:"GitHub Pages 공개 데모는 읽기 전용입니다. 서버 대시보드에서 실행하세요."}});
    if (path === "/api/config") return {{data_mode:"demo", capabilities:{{live_account:false,trading:false,control:false}}, dry_run:true, allow_live_trading:false, provider:"mock", model:"github-pages-static", tickers:["KRW-BTC","KRW-ETH"], coin_markets:MARKETS, intervals:[{{value:"minute15",label:"15분"}},{{value:"minute60",label:"1시간"}},{{value:"day",label:"일봉"}}], risk:{{max_order_krw:10000,min_order_krw:5000,max_daily_loss_krw:30000,min_confidence:0.6,cycle_seconds:600}}}};
    if (path === "/api/state") return state();
    if (path === "/api/pnl") return pnl();
    // ai-live.js가 window.__initialCoinPortfolio를 최신 스냅샷으로 바꿔치기하므로
    // 상수 대신 전역을 읽어 갱신이 전파되게 한다.
    if (path === "/api/portfolio") return clone(window.__initialCoinPortfolio || DEMO_PORTFOLIO);
    if (path === "/api/manual_portfolio") return {{items:[]}};
    if (path === "/api/manual_order/preview") return readOnly();
    if (path === "/api/manual_order" || path === "/api/control" || path === "/api/run_ai") return readOnly();
    if (path === "/api/decisions") return {{items:state().history}};
    if (path === "/api/trades") {{
      const snap = window.__aiTradeSnapshot || {{}};
      return {{items: snap.trades || []}};
    }};
    if (path === "/api/candles") return candles(params.get("ticker") || "KRW-BTC", params.get("interval") || "minute60", params.get("count") || 120);
    if (path === "/api/stocks/candles") return candles(params.get("code") || "005930", params.get("timeframe") || "day", params.get("count") || 120);
    if (path === "/api/coin/quote") return {{ticker:params.get("ticker") || "KRW-BTC", price:priceFor(params.get("ticker")), timestamp:now()}};
    if (path === "/api/coin/orderbook") return orderbook(params.get("ticker") || "KRW-BTC");
    if (path === "/api/coin/mini_charts") {{
      const tickers = (params.get("tickers") || MARKETS.map(m => m.market).join(",")).split(",").filter(Boolean);
      return {{items:tickers.map(t => {{ const c = candles(t, params.get("interval") || "minute60", params.get("count") || 36); return {{ticker:t, symbol:symbol(t), korean_name:(MARKETS.find(m=>m.market===t)||{{}}).korean_name || symbol(t), english_name:(MARKETS.find(m=>m.market===t)||{{}}).english_name || symbol(t), price:priceFor(t), change_pct:CHANGES[t] || 0, closes:c.closes, ok:true}}; }}), interval:params.get("interval") || "minute60", cached:true}};
    }}
    if (path === "/api/coin/news") return news();
    if (path === "/api/coin/news_summary") return newsSummary();
    if (path === "/api/stocks/quote") return {{code:params.get("code") || "005930", name:"삼성전자", price:74200, change:800, change_pct:1.09, timestamp:now()}};
    if (path === "/api/stocks/ai") return window.__stockAiSnapshot || {{error:"주식 AI 스냅샷 없음"}};
    if (path === "/api/stocks/watchlist") return {{items:(window.__stockAiSnapshot || {{}}).watchlist || [], updated_at:(window.__stockAiSnapshot || {{}}).generated_at || null}};
    if (path === "/api/ticker_quotes") return {{items:Object.keys(PRICES).slice(0, 6).map(t => ({{sym:symbol(t), price:PRICES[t], chg_pct:CHANGES[t] || 0}}))}};
    if (path === "/api/analyze_context") return {{ticker:params.get("ticker") || "KRW-BTC", holding:DEMO_PORTFOLIO.holdings[1], position:null, decisions:state().history, trades:[], portfolio_summary:DEMO_PORTFOLIO.summary}};
    if (path === "/api/analyze") return analyze(params.get("ticker") || "KRW-BTC");
    return {{}};
  }}

  const nativeFetch = window.fetch.bind(window);
  window.__stockagentNativeFetch = nativeFetch;
  window.fetch = async (input, init={{}}) => {{
    const raw = typeof input === "string" ? input : input.url;
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return nativeFetch(input, init);
    const path = url.pathname.replace(/^\\/stockagent/, "");
    if (path.startsWith("/api/")) {{
      const payload = route(path, url.searchParams, init);
      const status = Number(payload.__status || 200);
      delete payload.__status;
      const body = JSON.stringify(payload);
      return new Response(body, {{status, headers:{{"Content-Type":"application/json; charset=utf-8"}}}});
    }}
    return nativeFetch(input, init);
  }};
}})();
</script>
<!-- 실시간 뉴스 수집기: 목업 fetch 다음에 로드되어 코인/주식 뉴스를 실제 RSS로 대체 -->
<script src="/stockagent/news-live.js"></script>
"""


SNAPSHOT_FILE = DOCS / "data" / "ai_snapshot.json"
PORTFOLIO_FILE = DOCS / "data" / "portfolio_snapshot.json"
STOCK_AI_FILE = DOCS / "data" / "stock_snapshot.json"
COIN_MARKETS_FILE = DOCS / "data" / "coin_markets.json"
COIN_CANDLE_INTERVALS = ["minute60", "minute15", "day"]
COIN_CANDLE_COUNT = 120
COIN_SNAPSHOT_REUSE_SECONDS = 15 * 60  # 로컬 연속 export 시 재수집 생략
COIN_CANDLE_REFRESH_DEADLINE_SECONDS = max(
    10.0, float(os.getenv("COIN_CANDLE_REFRESH_DEADLINE_SECONDS", "45"))
)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _sig6(value: float) -> float | int:
    """유효숫자 6자리 반올림, 정수는 int로 — 스냅샷 JSON 용량 절감(차트 표시에는 충분)."""
    v = float(f"{float(value):.6g}")
    return int(v) if v == int(v) else v


def _snapshot_fresh(path: Path) -> bool:
    data = _read_json(path)
    if not data:
        return False
    try:
        ts = float(data.get("generated_ts") or 0)
    except (TypeError, ValueError):
        return False
    import time
    return time.time() - ts < COIN_SNAPSHOT_REUSE_SECONDS


def _coin_snapshot_refresh_enabled() -> bool:
    """Allow deterministic local exports while scheduled Pages builds refresh."""
    return os.getenv("COIN_SNAPSHOT_REFRESH", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }


def coin_markets_snapshot() -> list[dict]:
    """업비트 KRW 마켓 전체 목록 + 배포 시점 시세 스냅샷.

    브라우저(Origin) 요청은 업비트가 분당 몇 회 수준으로 제한하고 CORS 프록시도
    차단하므로, 목록·초기 시세는 export 시점에 서버측(제한 10회/초)에서 받아
    docs/data/coin_markets.json 으로 심는다. 실시간 갱신은 coin-live.js가
    웹소켓(브라우저 허용 확인됨)으로 처리한다.
    """
    import time

    cached = _read_json(COIN_MARKETS_FILE)
    if not _coin_snapshot_refresh_enabled() and cached and cached.get("markets"):
        return cached["markets"]
    if _snapshot_fresh(COIN_MARKETS_FILE):
        return cached["markets"]

    try:
        raw = json.loads(web._urlopen_text(
            "https://api.upbit.com/v1/market/all?isDetails=false", timeout=10))
        rows = [r for r in raw if isinstance(r, dict)
                and str(r.get("market", "")).upper().startswith("KRW-")]
        if not rows:
            raise ValueError("KRW 마켓 없음")

        prices: dict[str, float] = {}
        changes: dict[str, float] = {}
        volumes: dict[str, float] = {}
        try:
            tickers = json.loads(web._urlopen_text(
                "https://api.upbit.com/v1/ticker/all?quote_currencies=KRW", timeout=10))
            for t in tickers:
                m = str(t.get("market", "")).upper()
                if not m.startswith("KRW-"):
                    continue
                if t.get("trade_price") is not None:
                    prices[m] = _sig6(t["trade_price"])
                if t.get("signed_change_rate") is not None:
                    changes[m] = round(float(t["signed_change_rate"]) * 100, 3)
                if t.get("acc_trade_price_24h") is not None:
                    volumes[m] = float(t["acc_trade_price_24h"])
        except Exception:  # noqa: BLE001 - 시세 없이 목록만이라도 유지
            pass

        by_market = {}
        for r in rows:
            market = str(r["market"]).upper()
            by_market[market] = {
                "market": market,
                "symbol": market.replace("KRW-", ""),
                "korean_name": r.get("korean_name") or market.replace("KRW-", ""),
                "english_name": r.get("english_name") or market.replace("KRW-", ""),
            }
        priority = [m["market"] for m in MARKETS if m["market"] in by_market]
        rest = sorted((m for m in by_market if m not in priority),
                      key=lambda m: -volumes.get(m, 0.0))
        ordered = [by_market[m] for m in priority + rest]

        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "generated_ts": time.time(),
            "markets": ordered,
            "prices": prices,
            "changes": changes,
        }
        COIN_MARKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        COIN_MARKETS_FILE.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return ordered
    except Exception:  # noqa: BLE001 - CI에서 업비트 접근 실패 시 커밋된 스냅샷 재사용
        cached = _read_json(COIN_MARKETS_FILE)
        if cached and cached.get("markets"):
            return cached["markets"]
        return MARKETS


def coin_candles_snapshot(markets: list[dict]) -> None:
    """전 KRW 마켓의 캔들 종가 스냅샷(docs/data/coin_candles_*.json).

    미니 차트·메인 차트가 배포 시점까지의 실제 종가를 그리고, 마지막 값만
    coin-live.js가 웹소켓 실시간가로 덮어쓴다. web._upbit_candle_closes의
    스로틀(초당 ~9.5회)을 그대로 사용한다.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    if not _coin_snapshot_refresh_enabled():
        return

    codes = [m["market"] for m in markets]
    for interval in COIN_CANDLE_INTERVALS:
        out_file = DOCS / "data" / f"coin_candles_{interval}.json"
        if _snapshot_fresh(out_file):
            continue

        deadline = time.monotonic() + COIN_CANDLE_REFRESH_DEADLINE_SECONDS

        def one(market: str) -> tuple[str, list[float]]:
            for attempt in range(2):
                if time.monotonic() >= deadline:
                    break
                try:
                    closes = web._upbit_candle_closes(market, interval, COIN_CANDLE_COUNT)
                    if closes:
                        return market, [_sig6(v) for v in closes]
                except Exception:  # noqa: BLE001
                    time.sleep(0.4)
            return market, []

        fresh_closes: dict[str, list[float]] = {}
        for start in range(0, len(codes), 6):
            if time.monotonic() >= deadline:
                break
            batch = codes[start:start + 6]
            # 한 배치만 기다리므로 장애 시에도 deadline + 요청 timeout 범위에서 끝난다.
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results = pool.map(one, batch)
                for market, closes in results:
                    if closes:
                        fresh_closes[market] = closes

        existing = _read_json(out_file) or {}
        closes_map = dict(existing.get("closes") or {})
        closes_map.update(fresh_closes)

        # 신규 빌드에서 절반도 못 받았으면 불완전한 파일을 만들지 않는다. 기존 파일이
        # 있으면 성공분만 merge해 나머지 전체 종목의 마지막 정상 캔들을 보존한다.
        if len(closes_map) < max(1, len(codes) // 2):
            continue
        if not fresh_closes:
            continue

        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps({
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "generated_ts": time.time(),
            "interval": interval,
            "count": COIN_CANDLE_COUNT,
            "fresh_count": len(fresh_closes),
            "market_count": len(codes),
            "partial": len(fresh_closes) < len(codes),
            "closes": closes_map,
        }, ensure_ascii=False), encoding="utf-8")


def _demo_snapshot(template: dict) -> dict:
    """Return a detached, timestamped copy of a public-safe demo payload."""
    from datetime import datetime, timezone

    payload = json.loads(json.dumps(template, ensure_ascii=False))
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    return payload


def portfolio_snapshot() -> dict:
    """Return synthetic data only; public builds must never read a live account."""
    return _demo_snapshot(DEMO_PORTFOLIO)


def ai_trade_snapshot() -> dict:
    """Return synthetic data only; public builds must never read the trading DB."""
    return _demo_snapshot(DEMO_AI_SNAPSHOT)


def stock_ai_snapshot() -> dict:
    """Return synthetic data only; public builds must never read a brokerage account."""
    return _demo_snapshot(DEMO_STOCK_SNAPSHOT)


def _assert_public_snapshot_safe(payload: dict, *, name: str) -> None:
    """Fail the export if a future change tries to publish account/order material."""
    forbidden_keys = {
        "access_key", "secret_key", "access_key_enc", "secret_key_enc",
        "raw_result", "uuid", "order_uuid", "session_token",
    }

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in forbidden_keys:
                    raise RuntimeError(f"{name}: 공개 금지 필드 발견: {path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(payload, name)
    if payload.get("data_mode") != "demo":
        raise RuntimeError(f"{name}: data_mode=demo만 공개할 수 있습니다.")


def _write_public_snapshot(path: Path, payload: dict, *, name: str) -> None:
    _assert_public_snapshot_safe(payload, name=name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def page(html: str, initial_portfolio: bool = False, stocks_live: bool = False,
         ai_snapshot: dict | None = None, portfolio: dict | None = None,
         stock_ai: dict | None = None) -> str:
    pf = portfolio if portfolio is not None else DEMO_PORTFOLIO
    pf_json = json.dumps(pf, ensure_ascii=False, default=str).replace("</", "<\\/")
    if initial_portfolio:
        html = html.replace(
            "<!-- INITIAL_COIN_PORTFOLIO -->",
            f"<script>window.__initialCoinPortfolio = {pf_json};</script>",
        )
    if ai_snapshot is not None:
        payload = json.dumps(ai_snapshot, ensure_ascii=False, default=str).replace("</", "<\\/")
        html = html.replace(
            "<!-- AI_TRADE_SNAPSHOT -->",
            f"<script>window.__aiTradeSnapshot = {payload};</script>",
        )
    if stock_ai is not None:
        payload = json.dumps(stock_ai, ensure_ascii=False, default=str).replace("</", "<\\/")
        html = html.replace(
            "<!-- STOCK_AI_SNAPSHOT -->",
            f"<script>window.__stockAiSnapshot = {payload};</script>",
        )
    html = html.replace("<body>", "<body>\n" + STATIC_API.replace("__PORTFOLIO_JSON__", pf_json), 1)
    html = html.replace('href="/stocks"', 'href="/stockagent/stocks/"')
    html = html.replace('href="/coin"', 'href="/stockagent/coin/"')
    html = html.replace('href="/assets"', 'href="/stockagent/assets/"')
    html = html.replace('href="/analyze"', 'href="/stockagent/analyze/"')
    html = html.replace('href="/api/export/decisions.csv"', 'href="#"')
    html = html.replace('href="/api/export/trades.csv"', 'href="#"')
    html = html.replace('href="/static/', 'href="/stockagent/static/')
    html = html.replace('src="/static/', 'src="/stockagent/static/')
    html = html.replace("url('/static/", "url('/stockagent/static/")
    # HTS 캔들차트: 인라인 renderChart(선 차트)를 오버라이드하도록 본문 맨 끝에 로드
    html = html.replace(
        "</body>",
        '<script src="/stockagent/chart-hts.js"></script>\n</body>',
        1,
    )
    # 업비트 전체 마켓 + 실시간 시세(웹소켓): 목업·chart-hts 다음에 로드
    html = html.replace(
        "</body>",
        f'<script src="/stockagent/coin-live.js?v={_asset_version("coin-live.js")}"></script>\n</body>',
        1,
    )
    if stocks_live:
        # 실시간 주식 시세(Yahoo Finance + CORS 프록시): 목업·chart-hts 다음에 로드
        html = html.replace(
            "</body>",
            '<script src="/stockagent/stocks-live.js"></script>\n</body>',
            1,
        )
    if initial_portfolio:
        # 포트폴리오 실시간 평가: coin-live의 공유 웹소켓 시세로 평가액 갱신
        html = html.replace(
            "</body>",
            f'<script src="/stockagent/portfolio-live.js?v={_asset_version("portfolio-live.js")}"></script>\n</body>',
            1,
        )
        # AI 거래 탭 실시간 계좌: 웹소켓 시세 재평가 + 스냅샷 자동 새로고침
        html = html.replace(
            "</body>",
            f'<script src="/stockagent/ai-live.js?v={_asset_version("ai-live.js")}"></script>\n</body>',
            1,
        )
    return html


def write(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def main() -> None:
    snapshot = ai_trade_snapshot()
    pf = portfolio_snapshot()
    stocks = stock_ai_snapshot()
    _write_public_snapshot(SNAPSHOT_FILE, snapshot, name="ai_snapshot")
    _write_public_snapshot(PORTFOLIO_FILE, pf, name="portfolio_snapshot")
    _write_public_snapshot(STOCK_AI_FILE, stocks, name="stock_snapshot")
    markets = coin_markets_snapshot()
    coin_candles_snapshot(markets)

    static_src = ROOT / "static"
    static_dst = DOCS / "static"
    if static_src.exists():
        if static_dst.exists():
            shutil.rmtree(static_dst)
        shutil.copytree(static_src, static_dst)

    write(DOCS / "index.html",
          page(web.COIN_HTML, initial_portfolio=True, ai_snapshot=snapshot, portfolio=pf))
    write(DOCS / "coin" / "index.html",
          page(web.COIN_HTML, initial_portfolio=True, ai_snapshot=snapshot, portfolio=pf))
    write(DOCS / "stocks" / "index.html",
          page(web.STOCKS_HTML, stocks_live=True, stock_ai=stocks))
    write(DOCS / "assets" / "index.html", page(web.DASHBOARD_HTML))
    write(DOCS / "analyze" / "index.html", page(web.ANALYZE_HTML))
    write(
        DOCS / "404.html",
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url=/stockagent/">
  <title>stockagent</title>
</head>
<body><a href="/stockagent/">Go to stockagent</a></body>
</html>
""",
    )
    (DOCS / ".nojekyll").touch()


if __name__ == "__main__":
    main()
