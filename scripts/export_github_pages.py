"""Export the real Flask-rendered UI shell for GitHub Pages.

GitHub Pages cannot run the Flask server or private Upbit/AI calls. This export
uses the same HTML/CSS/JS constants as the app, then injects a small static API
shim with safe demo data so the public page looks and behaves like the dashboard
without publishing account data or secrets.
"""
from __future__ import annotations

import json
import os
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
    "summary": {
        "total_principal": 300_800,
        "total_value": 120_366,
        "cash_value": 69,
        "coin_value": 120_297,
        "cash_ratio": 0.1,
        "unrealized_pnl": -180_434,
        "realized_pnl": 0,
        "total_pnl": -180_434,
        "total_return_pct": -59.98,
        "assets_count": 2,
        "largest_asset": "SOL",
        "largest_weight": 64.6,
    },
    "holdings": [
        {
            "currency": "SOL",
            "ticker": "KRW-SOL",
            "balance": 0.634,
            "avg_buy_price": 362_436,
            "principal": 229_790,
            "current_price": 122_000,
            "current_value": 77_348,
            "return_pct": -66.34,
            "weight": 64.6,
            "unrealized_pnl": -152_442,
            "realized_pnl": 0,
            "trades_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "last_trade_at": None,
        },
        {
            "currency": "BTC",
            "ticker": "KRW-BTC",
            "balance": 0.000457,
            "avg_buy_price": 155_373_956,
            "principal": 70_940,
            "current_price": 93_005_000,
            "current_value": 42_501,
            "return_pct": -40.09,
            "weight": 35.4,
            "unrealized_pnl": -28_439,
            "realized_pnl": 0,
            "trades_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "last_trade_at": None,
        },
        {
            "currency": "KRW",
            "ticker": "KRW",
            "balance": 69,
            "avg_buy_price": 1,
            "principal": 69,
            "current_price": 1,
            "current_value": 69,
            "return_pct": 0,
            "weight": 0.1,
            "unrealized_pnl": 0,
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


STATIC_API = f"""
<script>
(() => {{
  const DEMO_PORTFOLIO = {json.dumps(DEMO_PORTFOLIO, ensure_ascii=False)};
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
    return {{ ticker, timestamp: Date.now(), total_ask_size: 9.42, total_bid_size: 10.18, units }};
  }}

  function state() {{
    return {{
      mode: "DRY_RUN(모의)",
      provider: "mock",
      model: "github-pages-static",
      loop_running: true,
      bot_paused: false,
      started_at: "GitHub Pages",
      interval: 600,
      cycle_count: 7,
      last_update: new Date().toLocaleTimeString("ko-KR", {{hour:"2-digit", minute:"2-digit"}}),
      today_pnl: 0,
      total_pnl: 0,
      error: null,
      history: [
        {{time:"15:42:10", ticker:"KRW-BTC", price:priceFor("KRW-BTC"), rsi:58.2, trend:"up", change_pct:0.8, action:"HOLD", confidence:0.54, reasoning:"공개 데모 모드입니다. GitHub Pages에서는 실제 주문과 AI 호출을 실행하지 않습니다.", order:"none | demo"}},
        {{time:"15:40:05", ticker:"KRW-SOL", price:priceFor("KRW-SOL"), rsi:51.7, trend:"down", change_pct:0.2, action:"HOLD", confidence:0.48, reasoning:"모의 데이터로 대시보드 UI를 표시합니다.", order:"none | demo"}}
      ]
    }};
  }}

  function pnl() {{
    const daily = Array.from({{length: 14}}, (_, i) => ({{
      date: `07-${{String(i + 1).padStart(2, "0")}}`,
      realized_pnl: Math.round(Math.sin(i / 2) * 1800),
      trades_count: i % 3
    }}));
    return {{ today: 0, total: 0, today_trades: 0, win_rate: 0, daily, trades: [] }};
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
    if (path === "/api/config") return {{dry_run:true, allow_live_trading:false, provider:"mock", model:"github-pages-static", tickers:["KRW-BTC","KRW-SOL"], coin_markets:MARKETS, intervals:[{{value:"minute15",label:"15분"}},{{value:"minute60",label:"1시간"}},{{value:"day",label:"일봉"}}], risk:{{max_order_krw:10000,min_order_krw:5000,max_daily_loss_krw:30000,min_confidence:0.6,cycle_seconds:600}}}};
    if (path === "/api/state") return state();
    if (path === "/api/pnl") return pnl();
    if (path === "/api/portfolio") return clone(DEMO_PORTFOLIO);
    if (path === "/api/manual_portfolio") return {{items:[]}};
    if (path === "/api/manual_order/preview") return manualOrderPreview(init);
    if (path === "/api/manual_order") return {{...manualOrderPreview(init), result:{{dry_run:true}}, portfolio: clone(DEMO_PORTFOLIO)}};
    if (path === "/api/decisions") return {{items:state().history}};
    if (path === "/api/trades") return {{items:[]}};
    if (path === "/api/candles") return candles(params.get("ticker") || "KRW-BTC", params.get("interval") || "minute60", params.get("count") || 120);
    if (path === "/api/stocks/candles") return candles(params.get("code") || "005930", params.get("timeframe") || "day", params.get("count") || 120);
    if (path === "/api/coin/quote") return {{ticker:params.get("ticker") || "KRW-BTC", price:priceFor(params.get("ticker")), timestamp:now()}};
    if (path === "/api/coin/orderbook") return orderbook(params.get("ticker") || "KRW-BTC");
    if (path === "/api/coin/mini_charts") {{
      const tickers = (params.get("tickers") || MARKETS.map(m => m.market).join(",")).split(",").filter(Boolean).slice(0, 24);
      return {{items:tickers.map(t => {{ const c = candles(t, params.get("interval") || "minute60", params.get("count") || 36); return {{ticker:t, symbol:symbol(t), korean_name:(MARKETS.find(m=>m.market===t)||{{}}).korean_name || symbol(t), english_name:(MARKETS.find(m=>m.market===t)||{{}}).english_name || symbol(t), price:priceFor(t), change_pct:CHANGES[t] || 0, closes:c.closes, ok:true}}; }}), interval:params.get("interval") || "minute60", cached:true}};
    }}
    if (path === "/api/coin/news") return news();
    if (path === "/api/coin/news_summary") return newsSummary();
    if (path === "/api/stocks/quote") return {{code:params.get("code") || "005930", name:"삼성전자", price:74200, change:800, change_pct:1.09, timestamp:now()}};
    if (path === "/api/ticker_quotes") return {{items:Object.keys(PRICES).slice(0, 6).map(t => ({{sym:symbol(t), price:PRICES[t], chg_pct:CHANGES[t] || 0}}))}};
    if (path === "/api/analyze_context") return {{ticker:params.get("ticker") || "KRW-BTC", holding:DEMO_PORTFOLIO.holdings[1], position:null, decisions:state().history, trades:[], portfolio_summary:DEMO_PORTFOLIO.summary}};
    if (path === "/api/analyze") return analyze(params.get("ticker") || "KRW-BTC");
    return {{}};
  }}

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init={{}}) => {{
    const raw = typeof input === "string" ? input : input.url;
    const url = new URL(raw, window.location.origin);
    const path = url.pathname.replace(/^\\/stockagent/, "");
    if (path.startsWith("/api/")) {{
      const body = JSON.stringify(route(path, url.searchParams, init));
      return new Response(body, {{status:200, headers:{{"Content-Type":"application/json; charset=utf-8"}}}});
    }}
    return nativeFetch(input, init);
  }};
}})();
</script>
<!-- 실시간 뉴스 수집기: 목업 fetch 다음에 로드되어 코인/주식 뉴스를 실제 RSS로 대체 -->
<script src="/stockagent/news-live.js"></script>
"""


def page(html: str, initial_portfolio: bool = False, stocks_live: bool = False) -> str:
    if initial_portfolio:
        payload = json.dumps(DEMO_PORTFOLIO, ensure_ascii=False).replace("</", "<\\/")
        html = html.replace(
            "<!-- INITIAL_COIN_PORTFOLIO -->",
            f"<script>window.__initialCoinPortfolio = {payload};</script>",
        )
    html = html.replace("<body>", "<body>\n" + STATIC_API, 1)
    html = html.replace('href="/stocks"', 'href="/stockagent/stocks/"')
    html = html.replace('href="/coin"', 'href="/stockagent/coin/"')
    html = html.replace('href="/assets"', 'href="/stockagent/assets/"')
    html = html.replace('href="/analyze"', 'href="/stockagent/analyze/"')
    html = html.replace('href="/api/export/decisions.csv"', 'href="#"')
    html = html.replace('href="/api/export/trades.csv"', 'href="#"')
    # HTS 캔들차트: 인라인 renderChart(선 차트)를 오버라이드하도록 본문 맨 끝에 로드
    html = html.replace(
        "</body>",
        '<script src="/stockagent/chart-hts.js"></script>\n</body>',
        1,
    )
    if stocks_live:
        # 실시간 주식 시세(Yahoo Finance + CORS 프록시): 목업·chart-hts 다음에 로드
        html = html.replace(
            "</body>",
            '<script src="/stockagent/stocks-live.js"></script>\n</body>',
            1,
        )
    return html


def write(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def main() -> None:
    write(DOCS / "index.html", page(web.COIN_HTML, initial_portfolio=True))
    write(DOCS / "coin" / "index.html", page(web.COIN_HTML, initial_portfolio=True))
    write(DOCS / "stocks" / "index.html", page(web.STOCKS_HTML, stocks_live=True))
    write(DOCS / "assets" / "index.html", page(web.DASHBOARD_HTML))
    write(DOCS / "analyze" / "index.html", page(web.ANALYZE_HTML))
    write(
        DOCS / "404.html",
        """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url=/stockagent/">
  <title>stockagent</title>
</head>
<body><a href="/stockagent/">stockagent로 이동</a></body>
</html>
""",
    )
    (DOCS / ".nojekyll").touch()


if __name__ == "__main__":
    main()
