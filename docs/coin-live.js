/*
 * coin-live.js — GitHub Pages(정적)에서 업비트 KRW 마켓 "전체"를 실데이터로 보여주는 스크립트.
 *
 * 업비트 REST는 브라우저(Origin) 요청을 분당 몇 회 수준으로 제한하고 CORS 프록시도
 * 차단하므로 REST를 직접 부르지 않는다. 대신:
 *  - 마켓 목록·초기 시세·캔들 종가는 export 시점 스냅샷(docs/data/coin_markets.json,
 *    coin_candles_*.json)을 읽고,
 *  - 실시간 가격·전일대비는 업비트 웹소켓(wss://api.upbit.com/websocket/v1, 브라우저
 *    Origin 허용 확인됨) 한 연결로 전 종목을 구독해 갱신한다.
 *
 * 기존 UI가 호출하는 /api/config(coin_markets) · /api/coin/mini_charts · /api/candles ·
 * /api/coin/quote · /api/coin/orderbook · /api/ticker_quotes 목업 응답을 대체하며,
 * 데이터가 없으면 원래(목업) 응답으로 폴백한다. 정적 목업 fetch 오버라이드와
 * chart-hts.js "다음"에 로드된다.
 */
(function () {
  "use strict";

  var prevFetch = window.fetch.bind(window);
  var BASE = window.location.pathname.indexOf("/stockagent") === 0 ? "/stockagent" : "";
  var WS_URL = "wss://api.upbit.com/websocket/v1";
  var TAPE_CODES = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-DOGE", "KRW-ADA"];

  // ---- 배포 시점 스냅샷 로드 -------------------------------------------------
  var marketsPromise = null;   // {markets, prices, changes, generated_at}
  var candlePromises = {};     // interval → Promise<{closes:{market:[...]}, count}>

  function loadMarkets() {
    if (!marketsPromise) {
      marketsPromise = prevFetch(BASE + "/data/coin_markets.json")
        .then(function (res) {
          if (!res.ok) throw new Error("coin_markets HTTP " + res.status);
          return res.json();
        })
        .then(function (data) {
          if (!data || !Array.isArray(data.markets) || !data.markets.length) {
            throw new Error("coin_markets 스냅샷 없음");
          }
          data.prices = data.prices || {};
          data.changes = data.changes || {};
          return data;
        })
        .catch(function (e) { marketsPromise = null; throw e; });
    }
    return marketsPromise;
  }

  function loadCandles(interval) {
    var key = /^minute(1|3|5|10|15|30|60)$|^day$/.test(interval) ? interval : "minute60";
    if (!candlePromises[key]) {
      candlePromises[key] = prevFetch(BASE + "/data/coin_candles_" + key + ".json")
        .then(function (res) {
          if (!res.ok) throw new Error("candles HTTP " + res.status);
          return res.json();
        })
        .then(function (data) {
          if (!data || !data.closes) throw new Error("캔들 스냅샷 없음");
          return data;
        })
        .catch(function (e) { delete candlePromises[key]; throw e; });
    }
    return candlePromises[key];
  }

  // ---- 업비트 웹소켓: 전 종목 ticker + 선택 종목 orderbook --------------------
  var ws = null;
  var wsCodes = [];              // 구독할 전체 마켓 코드
  var wsOrderbookCode = null;    // 호가를 구독 중인 마켓
  var wsRetryMs = 1000;
  var wsStarted = false;
  var livePrices = {};           // market → {price, change_pct, ts}
  var liveOrderbooks = {};       // market → 호가 payload

  function wsSubscribe() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !wsCodes.length) return;
    var req = [{ ticket: "stockagent-pages" }, { type: "ticker", codes: wsCodes }];
    if (wsOrderbookCode) req.push({ type: "orderbook", codes: [wsOrderbookCode] });
    req.push({ format: "DEFAULT" });
    try { ws.send(JSON.stringify(req)); } catch (e) { /* 재연결 루프가 처리 */ }
  }

  function handleMessage(d) {
    if (!d || !d.code) return;
    if (d.type === "ticker" && d.trade_price != null) {
      livePrices[d.code] = {
        price: Number(d.trade_price),
        change_pct: d.signed_change_rate != null ? Number(d.signed_change_rate) * 100 : null,
        ts: Date.now()
      };
    } else if (d.type === "orderbook" && Array.isArray(d.orderbook_units)) {
      liveOrderbooks[d.code] = {
        ticker: d.code,
        timestamp: d.timestamp || Date.now(),
        total_ask_size: d.total_ask_size,
        total_bid_size: d.total_bid_size,
        units: d.orderbook_units,
        ts: Date.now()
      };
    }
  }

  function wsConnect() {
    try { ws = new WebSocket(WS_URL); } catch (e) { scheduleReconnect(); return; }
    ws.binaryType = "arraybuffer";
    ws.onopen = function () {
      wsRetryMs = 1000;
      wsSubscribe();
    };
    ws.onmessage = function (ev) {
      try {
        var text = typeof ev.data === "string"
          ? ev.data
          : new TextDecoder("utf-8").decode(ev.data);
        handleMessage(JSON.parse(text));
      } catch (e) { /* 조각 메시지 등은 무시 */ }
    };
    ws.onclose = scheduleReconnect;
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
  }

  function scheduleReconnect() {
    ws = null;
    setTimeout(function () { if (wsStarted) wsConnect(); }, wsRetryMs);
    wsRetryMs = Math.min(wsRetryMs * 2, 30000);
  }

  function ensureWs() {
    if (wsStarted) return;
    wsStarted = true;
    loadMarkets().then(function (data) {
      wsCodes = data.markets.map(function (m) { return m.market; });
      wsConnect();
    }).catch(function () { wsStarted = false; });
  }

  function setOrderbookCode(code) {
    if (wsOrderbookCode === code) return;
    wsOrderbookCode = code;
    wsSubscribe();
  }

  function waitFor(check, timeoutMs) {
    return new Promise(function (resolve) {
      var t0 = Date.now();
      (function poll() {
        var v = check();
        if (v != null) return resolve(v);
        if (Date.now() - t0 >= timeoutMs) return resolve(null);
        setTimeout(poll, 120);
      })();
    });
  }

  // ---- 시세 헬퍼 --------------------------------------------------------------
  function priceOf(market, snap) {
    var live = livePrices[market];
    if (live) return live.price;
    return snap && snap.prices[market] != null ? Number(snap.prices[market]) : null;
  }

  function changeOf(market, snap) {
    var live = livePrices[market];
    if (live && live.change_pct != null) return live.change_pct;
    return snap && snap.changes[market] != null ? Number(snap.changes[market]) : null;
  }

  function sma(values, period) {
    return values.map(function (_, i) {
      if (i + 1 < period) return null;
      var s = 0;
      for (var k = i + 1 - period; k <= i; k++) s += values[k];
      return s / period;
    });
  }

  function rsiSeries(values) {
    return values.map(function (_, i) {
      if (i < 14) return 50;
      var gain = 0, loss = 0;
      for (var k = i - 13; k <= i; k++) {
        var d = values[k] - values[k - 1];
        if (d >= 0) gain += d; else loss -= d;
      }
      if (loss === 0) return 100;
      return 100 - 100 / (1 + gain / loss);
    });
  }

  function jsonResponse(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200, headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  // ---- /api/config: coin_markets를 전체 목록으로 교체 --------------------------
  function liveConfig(input, init) {
    return Promise.all([prevFetch(input, init), loadMarkets()])
      .then(function (pair) {
        return pair[0].json().then(function (cfg) {
          cfg.coin_markets = pair[1].markets;
          return jsonResponse(cfg);
        });
      });
  }

  // ---- /api/coin/mini_charts ---------------------------------------------------
  function liveMiniCharts(params) {
    var interval = params.get("interval") || "minute60";
    var count = Math.max(16, Math.min(Number(params.get("count") || 36), 80));
    var tickers = (params.get("tickers") || "").split(",")
      .map(function (t) { return t.trim().toUpperCase(); })
      .filter(function (t) { return t.indexOf("KRW-") === 0; })
      .slice(0, 24);
    ensureWs();
    return Promise.all([
      loadMarkets(),
      loadCandles(interval).catch(function () { return null; }),
      waitFor(function () { return Object.keys(livePrices).length ? true : null; }, 1200)
    ]).then(function (res) {
      var snap = res[0], candles = res[1];
      var byMarket = {};
      snap.markets.forEach(function (m) { byMarket[m.market] = m; });
      if (!tickers.length) tickers = snap.markets.slice(0, 18).map(function (m) { return m.market; });
      var items = tickers.map(function (market) {
        var meta = byMarket[market] || { symbol: market.replace("KRW-", "") };
        var closes = candles && candles.closes[market] ? candles.closes[market].slice(-count) : [];
        var price = priceOf(market, snap);
        if (closes.length && price != null) closes[closes.length - 1] = price;
        if (price == null && closes.length) price = closes[closes.length - 1];
        return {
          ticker: market,
          symbol: meta.symbol || market.replace("KRW-", ""),
          korean_name: meta.korean_name || meta.symbol || market,
          english_name: meta.english_name || meta.symbol || market,
          price: price,
          change_pct: changeOf(market, snap),
          closes: closes,
          ok: closes.length >= 2
        };
      });
      return jsonResponse({
        items: items, interval: interval, count: count, live: true,
        generated_at: new Date().toISOString(), cached: false
      });
    });
  }

  // ---- /api/candles (메인 차트) --------------------------------------------------
  function liveCandles(params) {
    var ticker = (params.get("ticker") || "KRW-BTC").toUpperCase();
    var interval = params.get("interval") || "minute60";
    var count = Math.max(30, Math.min(Number(params.get("count") || 120), 200));
    ensureWs();
    return Promise.all([loadMarkets(), loadCandles(interval)]).then(function (res) {
      var snap = res[0], candles = res[1];
      var closes = candles.closes[ticker];
      if (!closes || closes.length < 2) throw new Error(ticker + " 캔들 스냅샷 없음");
      closes = closes.slice(-count);
      var price = priceOf(ticker, snap);
      if (price != null) closes[closes.length - 1] = price;
      return jsonResponse({
        ticker: ticker, interval: interval, closes: closes,
        ma5: sma(closes, 5), ma20: sma(closes, 20), rsi: rsiSeries(closes),
        cached: false, live: true
      });
    });
  }

  // ---- /api/coin/quote -----------------------------------------------------------
  function liveQuote(params) {
    var ticker = (params.get("ticker") || "KRW-BTC").toUpperCase();
    ensureWs();
    return loadMarkets().then(function (snap) {
      return waitFor(function () {
        return livePrices[ticker] ? livePrices[ticker].price : null;
      }, 2500).then(function (live) {
        var price = live != null ? live : priceOf(ticker, snap);
        if (price == null) throw new Error(ticker + " 시세 없음");
        return jsonResponse({
          ticker: ticker, price: price, live: live != null,
          timestamp: new Date().toISOString()
        });
      });
    });
  }

  // ---- /api/coin/orderbook ---------------------------------------------------------
  function liveOrderbook(params) {
    var ticker = (params.get("ticker") || "KRW-BTC").toUpperCase();
    ensureWs();
    setOrderbookCode(ticker);
    return waitFor(function () {
      var ob = liveOrderbooks[ticker];
      return ob && Date.now() - ob.ts < 30000 ? ob : null;
    }, 4000).then(function (ob) {
      if (!ob) throw new Error(ticker + " 호가 없음");
      return jsonResponse({
        ticker: ob.ticker, timestamp: ob.timestamp,
        total_ask_size: ob.total_ask_size, total_bid_size: ob.total_bid_size,
        units: ob.units, live: true
      });
    });
  }

  // ---- /api/ticker_quotes (헤더 테이프) ----------------------------------------------
  function liveTape() {
    ensureWs();
    return Promise.all([
      loadMarkets(),
      waitFor(function () { return livePrices[TAPE_CODES[0]] ? true : null; }, 1500)
    ]).then(function (res) {
      var snap = res[0];
      var items = [];
      TAPE_CODES.forEach(function (market) {
        var price = priceOf(market, snap);
        if (price == null) return;
        items.push({
          sym: market.replace("KRW-", ""),
          price: price,
          chg_pct: Number((changeOf(market, snap) || 0).toFixed(2))
        });
      });
      if (!items.length) throw new Error("테이프 시세 없음");
      return jsonResponse({ items: items, live: true });
    });
  }

  // ---- fetch 오버라이드 ------------------------------------------------------------
  window.fetch = function (input, init) {
    try {
      var raw = typeof input === "string" ? input : input.url;
      var url = new URL(raw, window.location.origin);
      var path = url.pathname.replace(/^\/stockagent/, "");
      var fallback = function () { return prevFetch(input, init); };
      if (path === "/api/config") return liveConfig(input, init).catch(fallback);
      if (path === "/api/coin/mini_charts") return liveMiniCharts(url.searchParams).catch(fallback);
      if (path === "/api/candles") return liveCandles(url.searchParams).catch(fallback);
      if (path === "/api/coin/quote") return liveQuote(url.searchParams).catch(fallback);
      if (path === "/api/coin/orderbook") return liveOrderbook(url.searchParams).catch(fallback);
      if (path === "/api/ticker_quotes" && !document.getElementById("stockSvg")) {
        return liveTape().catch(fallback);
      }
    } catch (e) { /* URL 파싱 실패 → 원래 fetch */ }
    return prevFetch(input, init);
  };

  // 코인 대시보드에서는 미리 연결해 첫 렌더부터 실시간가가 잡히게 한다.
  if (document.getElementById("coin-ticker") || document.getElementById("coin-market-board-grid")) {
    loadMarkets().catch(function () {});
    loadCandles("minute60").catch(function () {});
    ensureWs();
    // 인라인 loadCoinConfig()는 이 스크립트보다 먼저(목업 12종으로) 실행되므로,
    // 오버라이드 설치 후 다시 불러 전체 마켓 목록으로 갱신한다.
    if (typeof window.loadCoinConfig === "function") {
      loadMarkets().then(function () { window.loadCoinConfig(); }).catch(function () {});
    }
  }
})();
