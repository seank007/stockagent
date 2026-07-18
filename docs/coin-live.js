/*
 * coin-live.js — GitHub Pages(정적)에서 업비트 KRW 마켓 "전체"를 실데이터로 보여주는 스크립트.
 *
 * 업비트 REST는 브라우저(Origin) 요청을 분당 몇 회 수준으로 제한하고 CORS 프록시도
 * 차단하므로 REST를 직접 부르지 않는다. 대신:
 *  - 마켓 목록·초기 시세·캔들 종가는 export 시점 스냅샷(docs/data/coin_markets.json,
 *    coin_candles_*.json)을 읽고,
 *  - 실시간 가격·전일대비는 업비트 웹소켓(wss://api.upbit.com/websocket/v1, 브라우저
 *    Origin 허용 확인됨) 한 연결로 현재 화면·선택·보유 종목을 구독해 갱신한다.
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

  // ---- 업비트 웹소켓: 현재 화면 + 선택/보유 종목만 ticker, 선택 종목 orderbook ----
  // 전체 마켓을 한꺼번에 구독하지 않고 현재 필요한 집합만 250ms 디바운스로
  // 갱신한다. 연결 재시도는 브라우저 Origin 연결 제한을 지키기 위해 최소 11초
  // 간격으로 수행하고, 연결 내부 구독 메시지는 초당 4회 이하로 제한한다.
  var ws = null;
  var wsCodes = [];
  var wsOrderbookCode = null;    // 호가를 구독 중인 마켓
  var trackedCodes = [];         // 포트폴리오/AI 화면이 추가로 필요로 하는 종목
  var wsRetryMs = 11000;
  var wsStarted = false;
  var wsReconnectTimer = null;
  var wsNextConnectAt = 0;
  var wsSubscribeTimer = null;
  var wsLastSubscribeAt = 0;
  var wsConnectStartedAt = 0;
  var wsOpenedAt = 0;
  var lastWsMessageAt = 0;
  var wsState = "idle";
  var snapshotGeneratedAt = null;
  var lastMarketUpdateAt = 0;
  var lastMarketSource = "snapshot";
  var uiRefreshTimer = null;
  var livePrices = {};           // market → {price, change_pct, ts}
  var liveOrderbooks = {};       // market → 호가 payload
  var WS_SUBSCRIBE_INTERVAL_MS = 250;
  var WS_RECONNECT_MIN_MS = 11000;
  var WS_DEAD_MS = 30000;

  function uniqueCodes(rows) {
    var seen = {};
    return (rows || []).map(function (code) { return String(code || "").toUpperCase(); })
      .filter(function (code) {
        if (code.indexOf("KRW-") !== 0 || seen[code]) return false;
        seen[code] = true;
        return true;
      });
  }

  function visibleMarketCodes() {
    var rows = [];
    var cards = document.querySelectorAll("#coin-market-board-grid .coin-mini-card[data-ticker]");
    for (var i = 0; i < cards.length; i++) rows.push(cards[i].getAttribute("data-ticker"));
    return rows;
  }

  function desiredTickerCodes() {
    return uniqueCodes(TAPE_CODES.concat(
      [window._coinTicker, wsOrderbookCode], trackedCodes, visibleMarketCodes()
    ));
  }

  function sameCodes(a, b) {
    return a.length === b.length && a.every(function (code, idx) { return code === b[idx]; });
  }

  function syncSubscriptions(force) {
    var next = desiredTickerCodes();
    if (!sameCodes(next, wsCodes)) {
      wsCodes = next;
      queueWsSubscribe();
    } else if (force) {
      queueWsSubscribe();
    }
    return wsCodes.slice();
  }

  function queueWsSubscribe() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !wsCodes.length) return;
    var waitMs = Math.max(0, WS_SUBSCRIBE_INTERVAL_MS - (Date.now() - wsLastSubscribeAt));
    clearTimeout(wsSubscribeTimer);
    if (waitMs > 0) {
      wsSubscribeTimer = setTimeout(wsSubscribe, waitMs);
      return;
    }
    wsSubscribe();
  }

  function wsSubscribe() {
    if (!ws || ws.readyState !== WebSocket.OPEN || !wsCodes.length) return;
    var req = [{ ticket: "stockagent-pages" }, { type: "ticker", codes: wsCodes }];
    if (wsOrderbookCode) req.push({ type: "orderbook", codes: [wsOrderbookCode] });
    req.push({ format: "DEFAULT" });
    try {
      ws.send(JSON.stringify(req));
      wsLastSubscribeAt = Date.now();
      wsState = "subscribed";
    } catch (e) { /* 재연결 루프가 처리 */ }
  }

  function scheduleUiRefresh() {
    if (uiRefreshTimer) return;
    uiRefreshTimer = setTimeout(function () {
      uiRefreshTimer = null;
      refreshVisibleCards();
      var selected = livePrices[window._coinTicker || "KRW-BTC"];
      if (selected && typeof window.applyCoinLivePrice === "function") {
        window.applyCoinLivePrice(selected.price);
      } else if (selected && typeof applyCoinLivePrice === "function") {
        applyCoinLivePrice(selected.price);
      }
      refreshStatus();
    }, 800);
  }

  function rememberTicker(d, source) {
    var code = d.code || d.market;
    var price = d.trade_price;
    if (!code || price == null) return;
    var now = Date.now();
    livePrices[code] = {
      price: Number(price),
      change_pct: d.signed_change_rate != null ? Number(d.signed_change_rate) * 100 : null,
      ts: now,
      exchange_ts: Number(d.timestamp || now),
      source: source
    };
    lastMarketUpdateAt = now;
    lastMarketSource = source;
    scheduleUiRefresh();
  }

  function handleMessage(d) {
    if (!d || d.error) return false;
    lastWsMessageAt = Date.now();
    if (d.type === "ticker" && d.trade_price != null) {
      rememberTicker(d, "websocket");
    } else if (d.type === "orderbook" && Array.isArray(d.orderbook_units)) {
      liveOrderbooks[d.code] = {
        ticker: d.code,
        timestamp: d.timestamp || Date.now(),
        total_ask_size: d.total_ask_size,
        total_bid_size: d.total_bid_size,
        units: d.orderbook_units,
        ts: Date.now()
      };
      lastMarketUpdateAt = Date.now();
      lastMarketSource = "websocket";
      refreshStatus();
    }
    return true;
  }

  function queueWsConnect() {
    if (!wsStarted || ws) return;
    var waitMs = Math.max(0, wsNextConnectAt - Date.now());
    if (waitMs === 0) {
      wsConnect();
      return;
    }
    if (wsReconnectTimer) return;
    wsReconnectTimer = setTimeout(function () {
      wsReconnectTimer = null;
      if (wsStarted && !ws) wsConnect();
    }, waitMs);
  }

  function wsConnect() {
    if (!wsStarted || ws) return;
    if (Date.now() < wsNextConnectAt) {
      queueWsConnect();
      return;
    }
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
    wsNextConnectAt = Date.now() + WS_RECONNECT_MIN_MS;
    wsConnectStartedAt = Date.now();
    wsState = "connecting";
    var socket;
    try { socket = new WebSocket(WS_URL); } catch (e) { scheduleReconnect(); return; }
    ws = socket;
    socket.binaryType = "arraybuffer";
    socket.onopen = function () {
      if (ws !== socket) return;
      wsRetryMs = WS_RECONNECT_MIN_MS;
      wsConnectStartedAt = 0;
      wsOpenedAt = Date.now();
      lastWsMessageAt = 0;
      wsState = "open";
      syncSubscriptions();
      queueWsSubscribe();
      refreshStatus();
    };
    socket.onmessage = function (ev) {
      if (ws !== socket) return;
      try {
        var text = typeof ev.data === "string"
          ? ev.data
          : new TextDecoder("utf-8").decode(ev.data);
        var payload = JSON.parse(text);
        if (payload && payload.error) {
          wsState = "error";
          try { socket.close(); } catch (closeError) {}
          scheduleReconnect(socket);
          return;
        }
        handleMessage(payload);
      } catch (e) { /* 조각 메시지 등은 무시 */ }
    };
    socket.onclose = function () { scheduleReconnect(socket); };
    socket.onerror = function () {
      if (ws !== socket) return;
      wsState = "error";
      try { socket.close(); } catch (e) {}
      scheduleReconnect(socket);
    };
  }

  function scheduleReconnect(socket) {
    if (socket && ws !== socket) return;
    clearTimeout(wsReconnectTimer);
    clearTimeout(wsSubscribeTimer);
    ws = null;
    wsState = "reconnecting";
    refreshStatus();
    wsNextConnectAt = Math.max(
      wsNextConnectAt,
      Date.now() + Math.max(WS_RECONNECT_MIN_MS, wsRetryMs)
    );
    wsRetryMs = Math.min(wsRetryMs * 2, 60000);
    queueWsConnect();
  }

  // 백그라운드에서도 시세 연결은 유지하고 DOM 갱신만 생략한다.
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      if (wsStarted && !ws) queueWsConnect();
      syncSubscriptions();
      refreshVisibleCards();
      refreshStatus();
    }
  });

  function ensureWs() {
    if (wsStarted) {
      syncSubscriptions();
      if (!ws) queueWsConnect();
      return;
    }
    wsStarted = true;
    loadMarkets().then(function (data) {
      snapshotGeneratedAt = data.generated_at || data.generated_ts || null;
      syncSubscriptions();
      queueWsConnect();
    }).catch(function () { wsStarted = false; });
  }

  function setOrderbookCode(code) {
    if (wsOrderbookCode === code) return;
    wsOrderbookCode = code;
    // ticker 집합이 같아도 orderbook 타입의 code가 바뀌었으므로 반드시 재전송한다.
    syncSubscriptions(true);
  }

  function trackCodes(codes) {
    trackedCodes = uniqueCodes(codes);
    syncSubscriptions();
    ensureWs();
  }

  function formatMarketTime(value) {
    if (!value) return "—";
    var date = value instanceof Date ? value : new Date(
      typeof value === "string" && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value)
        ? value.replace(" ", "T") + ":00Z" : value
    );
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleTimeString("en-GB", {
      hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false
    });
  }

  function refreshStatus() {
    var upd = document.getElementById("upd");
    if (!upd) return;
    var connectionFresh = (wsState === "open" || wsState === "subscribed")
      && !!lastWsMessageAt && Date.now() - lastWsMessageAt <= WS_DEAD_MS;
    if (connectionFresh) {
      upd.textContent = "MARKET LIVE " + formatMarketTime(lastMarketUpdateAt);
    } else if (lastMarketUpdateAt) {
      upd.textContent = "MARKET STALE " + formatMarketTime(lastMarketUpdateAt);
    } else if (snapshotGeneratedAt) {
      upd.textContent = "SNAPSHOT " + formatMarketTime(snapshotGeneratedAt);
    } else {
      upd.textContent = wsState === "connecting" || wsState === "open"
        ? "MARKET CONNECTING" : "MARKET OFFLINE";
    }
  }

  function watchdogWs() {
    if (!ws) return;
    if (ws.readyState === WebSocket.CONNECTING || ws.readyState === 0) {
      if (wsConnectStartedAt && Date.now() - wsConnectStartedAt > WS_DEAD_MS) {
        wsState = "stale";
        var connectingSocket = ws;
        try { connectingSocket.close(); } catch (e) {}
        scheduleReconnect(connectingSocket);
      }
      return;
    }
    if (ws.readyState !== WebSocket.OPEN) {
      scheduleReconnect(ws);
      return;
    }
    var reference = lastWsMessageAt || wsOpenedAt;
    if (reference && Date.now() - reference > WS_DEAD_MS) {
      wsState = "stale";
      var staleSocket = ws;
      try { staleSocket.close(); } catch (e) {}
      scheduleReconnect(staleSocket);
    }
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

  function marketFreshness(market, snap) {
    var live = livePrices[market];
    if (live) {
      var connectionFresh = (wsState === "open" || wsState === "subscribed")
        && !!lastWsMessageAt && Date.now() - lastWsMessageAt <= WS_DEAD_MS;
      return {
        live: connectionFresh,
        stale: !connectionFresh,
        source: live.source,
        source_at: new Date(live.ts).toISOString()
      };
    }
    return {
      live: false,
      stale: true,
      source: "snapshot",
      source_at: snap && (snap.generated_at || snap.generated_ts) || null
    };
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
      .filter(function (t) { return t.indexOf("KRW-") === 0; });
    ensureWs();
    syncSubscriptions();
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
        var freshness = marketFreshness(market, snap);
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
          ok: closes.length >= 2,
          live: freshness.live,
          stale: freshness.stale,
          source: freshness.source,
          source_at: freshness.source_at
        };
      });
      var anyLive = items.some(function (item) { return item.live; });
      return jsonResponse({
        items: items, interval: interval, count: count, live: anyLive, stale: !anyLive,
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
      var freshness = marketFreshness(ticker, snap);
      if (price != null) closes[closes.length - 1] = price;
      return jsonResponse({
        ticker: ticker, interval: interval, closes: closes,
        ma5: sma(closes, 5), ma20: sma(closes, 20), rsi: rsiSeries(closes),
        cached: false, live: freshness.live, stale: freshness.stale,
        source: freshness.source, source_at: freshness.source_at
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
      }, 1600).then(function (live) {
        var price = live != null ? live : priceOf(ticker, snap);
        var freshness = marketFreshness(ticker, snap);
        if (price == null) throw new Error(ticker + " 시세 없음");
        return jsonResponse({
          ticker: ticker, price: price, live: freshness.live, stale: freshness.stale,
          source: freshness.source, timestamp: freshness.source_at
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
      var anyLive = false;
      TAPE_CODES.forEach(function (market) {
        var price = priceOf(market, snap);
        if (price == null) return;
        var freshness = marketFreshness(market, snap);
        if (freshness.live) anyLive = true;
        items.push({
          sym: market.replace("KRW-", ""),
          price: price,
          chg_pct: Number((changeOf(market, snap) || 0).toFixed(2)),
          live: freshness.live,
          stale: freshness.stale,
          source_at: freshness.source_at
        });
      });
      if (!items.length) throw new Error("테이프 시세 없음");
      return jsonResponse({ items: items, live: anyLive, stale: !anyLive });
    });
  }

  // ---- 보이는 미니 카드 실시간 갱신 -------------------------------------------------
  // 보드 재로드(3분)를 기다리지 않고, 웹소켓 시세로 카드의 가격·전일대비만 바로 바꾼다.
  function refreshVisibleCards() {
    if (document.hidden) return;
    if (window._coinSection && window._coinSection !== "market") return;
    var cards = document.querySelectorAll("#coin-market-board-grid .coin-mini-card[data-ticker]");
    for (var i = 0; i < cards.length; i++) {
      var live = livePrices[cards[i].getAttribute("data-ticker")];
      if (!live) continue;
      // 페이지의 KRW/PCT는 const 선언이라 window에 안 붙는다 — 전역 바인딩을 직접 참조
      var priceEl = cards[i].querySelector(".coin-mini-price");
      if (priceEl && typeof KRW === "function") {
        priceEl.textContent = KRW(live.price) + " 원";
      }
      var chgEl = cards[i].querySelector(".coin-mini-change");
      if (chgEl && live.change_pct != null && typeof PCT === "function") {
        chgEl.textContent = PCT(live.change_pct);
        chgEl.className = "coin-mini-change " +
          (live.change_pct > 0 ? "up" : live.change_pct < 0 ? "down" : "muted");
      }
    }
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

  // ai-live.js 등 다른 스크립트가 같은 웹소켓 시세를 재사용할 수 있게 공개한다.
  window.__coinLive = {
    prices: livePrices,
    ensureWs: ensureWs,
    syncSubscriptions: syncSubscriptions,
    trackCodes: trackCodes,
    setOrderbookCode: setOrderbookCode,
    refreshStatus: refreshStatus,
    checkHealth: watchdogWs,
    getSubscriptionCodes: function () { return wsCodes.slice(); },
    getStatus: function () {
      return {
        state: wsState,
        source: lastMarketSource,
        last_update_at: lastMarketUpdateAt || null,
        fresh: (wsState === "open" || wsState === "subscribed")
          && !!lastWsMessageAt && Date.now() - lastWsMessageAt <= WS_DEAD_MS
      };
    }
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
    var board = document.getElementById("coin-market-board-grid");
    if (board && window.MutationObserver) {
      new MutationObserver(function () { syncSubscriptions(); })
        .observe(board, { childList: true });
    }
    setInterval(refreshVisibleCards, 2000);
    setInterval(refreshStatus, 1000);
    setInterval(watchdogWs, 5000);
  }
})();
