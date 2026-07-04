/*
 * stocks-live.js — GitHub Pages(정적)에서 실시간 주식 시세를 붙이는 스크립트.
 *
 * 서버가 없으므로 Yahoo Finance 공개 chart/spark API를 news-live.js와 같은
 * CORS 프록시 폴백 체인으로 브라우저에서 직접 호출한다. 한국(.KS/.KQ)·미국·
 * 해외 지수·환율을 모두 지원하며, 기존 UI가 호출하는 /api/stocks/quote ·
 * /api/stocks/candles · /api/ticker_quotes 목업 응답을 실데이터로 대체한다.
 *
 * 이 스크립트는 정적 목업(fetch 오버라이드)·news-live.js "다음"에 로드되어
 * fetch를 한 번 더 감싼다. 라이브 호출이 실패하면 목업 응답으로 폴백한다.
 * 추가로 주식 페이지에는 지수/한국/미국 실시간 마켓 보드를 주입한다.
 */
(function () {
  "use strict";

  var prevFetch = window.fetch.bind(window);

  // ---- 공개 CORS 프록시 (news-live.js와 동일한 폴백 체인) -------------------
  var PROXIES = [
    { build: function (u) { return "https://api.codetabs.com/v1/proxy/?quest=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://corsproxy.io/?url=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.allorigins.win/raw?url=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.allorigins.win/get?url=" + encodeURIComponent(u); }, wrapped: true },
    { build: function (u) { return "https://thingproxy.freeboard.io/fetch/" + u; } }
  ];
  var HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"];
  var TRY_TIMEOUT_MS = 6500;
  var TOTAL_DEADLINE_MS = 16000;
  var QUOTE_TTL = 45000;        // 인트라데이(시세) 캐시
  var HIST_TTL = 5 * 60000;     // 일/주/월봉 캐시
  var BOARD_REFRESH_MS = 60000; // 마켓 보드 갱신 주기

  // ---- 종목/지수 사전 --------------------------------------------------------
  var KR_STOCKS = [
    ["005930", "삼성전자"], ["000660", "SK하이닉스"], ["373220", "LG에너지솔루션"],
    ["207940", "삼성바이오로직스"], ["005380", "현대차"], ["000270", "기아"],
    ["035420", "NAVER"], ["035720", "카카오"], ["005490", "POSCO홀딩스"],
    ["105560", "KB금융"], ["247540", "에코프로비엠"], ["196170", "알테오젠"]
  ];
  var US_STOCKS = [
    ["AAPL", "애플"], ["MSFT", "마이크로소프트"], ["NVDA", "엔비디아"],
    ["GOOGL", "알파벳"], ["AMZN", "아마존"], ["TSLA", "테슬라"],
    ["META", "메타"], ["AVGO", "브로드컴"], ["NFLX", "넷플릭스"], ["AMD", "AMD"]
  ];
  var INDICES = [
    ["^KS11", "코스피"], ["^KQ11", "코스닥"], ["^GSPC", "S&P 500"],
    ["^IXIC", "나스닥"], ["^DJI", "다우존스"], ["^SOX", "필라델피아 반도체"],
    ["^N225", "닛케이 225"], ["^HSI", "항셍"], ["KRW=X", "원/달러"]
  ];
  var NAME_OF = {};
  KR_STOCKS.concat(US_STOCKS, INDICES).forEach(function (p) { NAME_OF[p[0]] = p[1]; });

  // 상단 티커 테이프에 흐르는 심볼(마켓 보드 캐시에서 공급)
  var TAPE_CODES = ["^KS11", "^KQ11", "^GSPC", "^IXIC", "^DJI", "^N225", "KRW=X",
    "005930", "000660", "NVDA", "AAPL", "TSLA"];
  // 마켓 보드 구성
  var BOARD_GROUPS = [
    { label: "지수 · 환율", items: INDICES },
    { label: "한국 주식", items: KR_STOCKS.slice(0, 6) },
    { label: "미국 주식", items: US_STOCKS.slice(0, 6) }
  ];
  var BOARD_CODES = [];
  BOARD_GROUPS.forEach(function (g) { g.items.forEach(function (p) { BOARD_CODES.push(p[0]); }); });

  // ---- HTTP 유틸 -------------------------------------------------------------
  function fetchWithTimeout(url, wrapped) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, TRY_TIMEOUT_MS);
    return prevFetch(url, { signal: ctrl.signal, redirect: "follow" })
      .then(function (res) {
        clearTimeout(timer);
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.text();
      })
      .then(function (text) {
        var body = text;
        if (wrapped) {
          var j = JSON.parse(text);
          body = j && j.contents ? j.contents : "";
        }
        return JSON.parse(body);
      })
      .catch(function (e) { clearTimeout(timer); throw e; });
  }

  function withDeadline(promise, ms) {
    return new Promise(function (resolve, reject) {
      var to = setTimeout(function () { reject(new Error("deadline")); }, ms);
      promise.then(
        function (v) { clearTimeout(to); resolve(v); },
        function (e) { clearTimeout(to); reject(e); }
      );
    });
  }

  // spark API 구형 응답: {"AAPL": {symbol, close:[...], previousClose, ...}, ...}
  function looksLikeSparkMap(json) {
    if (!json || typeof json !== "object" || Array.isArray(json)) return false;
    var keys = Object.keys(json);
    if (!keys.length) return false;
    var v = json[keys[0]];
    return !!(v && typeof v === "object" && Array.isArray(v.close));
  }

  // 프록시×호스트를 순차 폴백하며 Yahoo JSON을 가져온다.
  // 성공한 프록시를 기억해 다음 호출부터 우선 시도한다(죽은 프록시 헛시도 방지).
  var goodProxyIdx = 0;
  function fetchYahoo(pathQuery) {
    var attempts = [];
    PROXIES.forEach(function (proxy, pi) {
      HOSTS.forEach(function (host) {
        // 프록시측 캐시 회피용 30초 단위 버스터
        var url = host + pathQuery + "&ts=" + Math.floor(Date.now() / 30000);
        attempts.push({ proxy: proxy, proxyIdx: pi, url: url });
      });
    });
    attempts.sort(function (a, b) {
      return (a.proxyIdx === goodProxyIdx ? 0 : 1) - (b.proxyIdx === goodProxyIdx ? 0 : 1);
    });
    var idx = 0;
    function attempt() {
      if (idx >= attempts.length) return Promise.reject(new Error("모든 프록시 실패"));
      var a = attempts[idx++];
      return fetchWithTimeout(a.proxy.build(a.url), a.proxy.wrapped)
        .then(function (json) {
          if (json && (json.chart || json.spark || looksLikeSparkMap(json))) return json;
          throw new Error("야후 응답 아님");
        })
        .then(function (json) { goodProxyIdx = a.proxyIdx; return json; })
        .catch(function () { return attempt(); });
    }
    return withDeadline(attempt(), TOTAL_DEADLINE_MS);
  }

  // ---- 차트 캐시 + 심볼 해석 --------------------------------------------------
  var chartCache = {};  // "SYM|range|interval" → {t, result}
  var RESOLVED = {};    // 입력 코드 → 야후 심볼 (예: "005930" → "005930.KS")

  function chartResultOf(json) {
    var r = json && json.chart && json.chart.result && json.chart.result[0];
    if (!r || !r.meta || r.meta.regularMarketPrice == null) return null;
    return r;
  }

  function getChart(symbol, range, interval, ttl) {
    var key = symbol + "|" + range + "|" + interval;
    var hit = chartCache[key];
    if (hit && Date.now() - hit.t < ttl) return Promise.resolve(hit.result);
    var path = "/v8/finance/chart/" + encodeURIComponent(symbol) +
      "?range=" + range + "&interval=" + interval + "&includePrePost=false";
    return fetchYahoo(path).then(function (json) {
      var result = chartResultOf(json);
      if (!result) throw new Error(symbol + " 결과 없음");
      chartCache[key] = { t: Date.now(), result: result };
      return result;
    });
  }

  // 입력 코드(6자리 한국코드/미국티커/^지수)를 야후 심볼로 바꿔 차트를 가져온다.
  function resolveChart(code, range, interval, ttl) {
    var candidates;
    if (RESOLVED[code]) candidates = [RESOLVED[code]];
    else if (/^\d{6}$/.test(code)) candidates = [code + ".KS", code + ".KQ"];
    else candidates = [code];
    var idx = 0;
    function attempt() {
      if (idx >= candidates.length) return Promise.reject(new Error(code + " 조회 실패"));
      var sym = candidates[idx++];
      return getChart(sym, range, interval, ttl)
        .then(function (result) { RESOLVED[code] = sym; return { symbol: sym, result: result }; })
        .catch(function (e) { if (idx < candidates.length) return attempt(); throw e; });
    }
    return attempt();
  }

  // ---- 지표 계산 --------------------------------------------------------------
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

  // ---- 표시 형식 ---------------------------------------------------------------
  function fmtNum(v, digits) {
    if (v == null || isNaN(v)) return "—";
    return Number(v).toLocaleString("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits > 0 && Math.abs(v) < 10000 ? Math.min(digits, 2) : 0 });
  }

  function priceLabel(code, v, currency, signed) {
    if (v == null || isNaN(v)) return "—";
    var sign = signed && v > 0 ? "+" : "";
    if (code && code.charAt(0) === "^") return sign + fmtNum(v, 2);
    if (currency === "USD") return sign + "$" + fmtNum(Math.abs(v) * (v < 0 ? -1 : 1), 2).replace("-", "-");
    if (currency === "JPY") return sign + fmtNum(v, 0) + " 엔";
    if (currency === "KRW") return sign + fmtNum(v, Math.abs(v) < 10000 ? 2 : 0) + " 원";
    return sign + fmtNum(v, 2) + (currency ? " " + currency : "");
  }

  function marketStatus(meta) {
    try {
      var reg = meta.currentTradingPeriod && meta.currentTradingPeriod.regular;
      var now = Date.now() / 1000;
      if (reg && now >= reg.start && now <= reg.end) return "장중";
      if (reg && now < reg.start) return "장전";
      return "장마감";
    } catch (e) { return "—"; }
  }

  function firstNonNull(arr) {
    if (!arr) return null;
    for (var i = 0; i < arr.length; i++) if (arr[i] != null) return arr[i];
    return null;
  }

  // ---- /api/stocks/quote -------------------------------------------------------
  function buildQuote(code, symbol, r) {
    var meta = r.meta;
    var q0 = (r.indicators && r.indicators.quote && r.indicators.quote[0]) || {};
    var price = meta.regularMarketPrice;
    var prev = meta.chartPreviousClose != null ? meta.chartPreviousClose : meta.previousClose;
    var change = prev != null ? price - prev : 0;
    var pct = prev ? (change / prev) * 100 : null;
    var cur = meta.currency || "";
    var open = firstNonNull(q0.open);
    var status = marketStatus(meta);
    var updated = meta.regularMarketTime
      ? new Date(meta.regularMarketTime * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
      : "—";
    var name = NAME_OF[code] || meta.shortName || meta.longName || code;
    return {
      code: code, symbol: symbol, name: name,
      price: price, change: change, change_pct: pct == null ? null : Number(pct.toFixed(2)),
      currency: cur, status: status,
      price_text: priceLabel(code, price, cur),
      change_text: priceLabel(code, change, cur, true),
      updated_at: "갱신 " + updated,
      rows: [
        { label: "시가", value: priceLabel(code, open, cur) },
        { label: "고가", value: priceLabel(code, meta.regularMarketDayHigh, cur) },
        { label: "저가", value: priceLabel(code, meta.regularMarketDayLow, cur) },
        { label: "전일종가", value: priceLabel(code, prev, cur) }
      ],
      metrics: [
        { label: "거래량", value: meta.regularMarketVolume != null ? fmtNum(meta.regularMarketVolume, 0) : "—" },
        { label: "52주 최고", value: priceLabel(code, meta.fiftyTwoWeekHigh, cur) },
        { label: "52주 최저", value: priceLabel(code, meta.fiftyTwoWeekLow, cur) },
        { label: "거래소", value: meta.fullExchangeName || meta.exchangeName || "—" }
      ],
      flow_note: "Yahoo Finance 실시간(거래소별 최대 15~20분 지연) · " +
        (meta.fullExchangeName || "") + " · " + status,
      live: true, timestamp: new Date().toISOString()
    };
  }

  // ---- /api/stocks/candles -------------------------------------------------------
  var FRAME_PARAMS = {
    day: { range: "1y", interval: "1d", ttl: HIST_TTL },
    week: { range: "5y", interval: "1wk", ttl: HIST_TTL },
    month: { range: "10y", interval: "1mo", ttl: HIST_TTL }
  };

  function buildCandles(code, symbol, r, timeframe, count) {
    var q0 = (r.indicators && r.indicators.quote && r.indicators.quote[0]) || {};
    var closesRaw = q0.close || [];
    var idxs = [];
    for (var i = 0; i < closesRaw.length; i++) if (closesRaw[i] != null) idxs.push(i);
    var pick = function (arr) { return idxs.map(function (i) { return arr && arr[i] != null ? arr[i] : null; }); };
    var closes = pick(closesRaw);
    var opens = pick(q0.open), highs = pick(q0.high), lows = pick(q0.low), volumes = pick(q0.volume);
    var n = Math.max(30, Math.min(Number(count || 140), 200));
    if (closes.length > n) {
      var cut = closes.length - n;
      closes = closes.slice(cut); opens = opens.slice(cut); highs = highs.slice(cut);
      lows = lows.slice(cut); volumes = volumes.slice(cut);
    }
    return {
      code: code, ticker: symbol, interval: timeframe, cached: false, live: true,
      closes: closes, opens: opens, highs: highs, lows: lows, volumes: volumes,
      ma5: sma(closes, 5), ma20: sma(closes, 20), rsi: rsiSeries(closes)
    };
  }

  // ---- 마켓 보드/테이프 데이터 (spark 배치 → 실패 시 개별 chart 폴백) ------------
  var boardCache = {};   // code → {price, prev, change_pct, closes, currency, t}
  var boardUpdatedAt = null;
  var boardLoading = false;

  function yahooSymbolFor(code) {
    if (RESOLVED[code]) return RESOLVED[code];
    if (/^\d{6}$/.test(code)) return code + ".KS"; // 보드 종목은 전부 KOSPI
    return code;
  }

  function codeOfSymbol(sym) {
    var m = String(sym).match(/^(\d{6})\.(KS|KQ)$/);
    return m ? m[1] : sym;
  }

  // 야후 응답에 통화 정보가 없을 때(spark 맵 포맷) 코드로 추정한다.
  function currencyHint(code) {
    if (/^\d{6}$/.test(code) || code === "KRW=X") return "KRW";
    if (code.charAt(0) === "^") return "";
    return "USD";
  }

  function storeBoardEntry(code, meta, closes) {
    var clean = (closes || []).filter(function (v) { return v != null; });
    var price = meta.regularMarketPrice != null ? meta.regularMarketPrice : clean[clean.length - 1];
    var prev = meta.chartPreviousClose != null ? meta.chartPreviousClose : meta.previousClose;
    if (price == null) return;
    boardCache[code] = {
      price: price, prev: prev,
      change_pct: prev ? ((price - prev) / prev) * 100 : 0,
      closes: clean,
      currency: meta.currency || currencyHint(code), t: Date.now()
    };
  }

  // spark는 요청당 심볼 20개 제한 → 18개씩 나눠 병렬 요청한다.
  function refreshBoardViaSpark() {
    var symbols = BOARD_CODES.map(yahooSymbolFor);
    var chunks = [];
    for (var i = 0; i < symbols.length; i += 18) chunks.push(symbols.slice(i, i + 18));
    return Promise.all(chunks.map(sparkChunk)).then(function (counts) {
      var total = counts.reduce(function (a, b) { return a + b; }, 0);
      if (!total) throw new Error("spark 비어있음");
      return true;
    });
  }

  function sparkChunk(symbols) {
    var path = "/v8/finance/spark?symbols=" + encodeURIComponent(symbols.join(",")) +
      "&range=1d&interval=15m";
    return fetchYahoo(path).then(function (json) {
      var stored = 0;
      var results = (json.spark && json.spark.result) || [];
      if (results.length) {
        // 신형 포맷: {spark:{result:[{symbol, response:[{meta, indicators}]}]}}
        results.forEach(function (item) {
          var resp = item.response && item.response[0];
          if (!resp || !resp.meta) return;
          var closes = resp.indicators && resp.indicators.quote && resp.indicators.quote[0]
            ? resp.indicators.quote[0].close : [];
          storeBoardEntry(codeOfSymbol(item.symbol), resp.meta, closes);
          stored++;
        });
      } else if (looksLikeSparkMap(json)) {
        // 구형 포맷: {"005930.KS": {close:[...], previousClose, chartPreviousClose}, ...}
        Object.keys(json).forEach(function (sym) {
          var v = json[sym];
          if (!v || !Array.isArray(v.close)) return;
          storeBoardEntry(codeOfSymbol(sym), {
            chartPreviousClose: v.chartPreviousClose != null ? v.chartPreviousClose : v.previousClose
          }, v.close);
          stored++;
        });
      }
      return stored;
    }).catch(function () { return 0; });
  }

  function refreshBoardViaCharts() {
    // spark 폴백: 테이프 우선순위 12심볼만 개별 조회(4개씩 순차)
    var codes = TAPE_CODES.slice();
    function chunkRun(start) {
      if (start >= codes.length) return Promise.resolve(true);
      var slice = codes.slice(start, start + 4).map(function (code) {
        return resolveChart(code, "1d", "15m", QUOTE_TTL).then(function (r) {
          var q0 = (r.result.indicators && r.result.indicators.quote && r.result.indicators.quote[0]) || {};
          storeBoardEntry(code, r.result.meta, q0.close || []);
        }).catch(function () {});
      });
      return Promise.all(slice).then(function () { return chunkRun(start + 4); });
    }
    return chunkRun(0);
  }

  function refreshBoard(force) {
    // 백그라운드 탭에서는 주기 갱신을 쉬되, 최초 로드(force)는 항상 실행한다.
    if (boardLoading || (document.hidden && !force)) return Promise.resolve(false);
    boardLoading = true;
    return refreshBoardViaSpark()
      .catch(function () { return refreshBoardViaCharts(); })
      .then(function (ok) {
        boardUpdatedAt = new Date();
        renderBoard();
        return ok;
      })
      .catch(function () { return false; })
      .then(function (v) { boardLoading = false; return v; });
  }

  // ---- fetch 오버라이드 -----------------------------------------------------------
  function jsonResponse(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200, headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  function cleanCode(raw) {
    var s = String(raw || "").trim().toUpperCase().replace(/[^A-Z0-9.^=\-]/g, "");
    return s || "005930";
  }

  function liveQuote(params) {
    var code = cleanCode(params.get("code"));
    return resolveChart(code, "1d", "5m", QUOTE_TTL).then(function (r) {
      return jsonResponse(buildQuote(code, r.symbol, r.result));
    });
  }

  function liveCandles(params) {
    var code = cleanCode(params.get("code"));
    var frame = FRAME_PARAMS[params.get("timeframe")] ? params.get("timeframe") : "day";
    var p = FRAME_PARAMS[frame];
    return resolveChart(code, p.range, p.interval, p.ttl).then(function (r) {
      return jsonResponse(buildCandles(code, r.symbol, r.result, frame, params.get("count")));
    });
  }

  function liveTape() {
    var items = [];
    TAPE_CODES.forEach(function (code) {
      var e = boardCache[code];
      if (!e) return;
      items.push({
        sym: NAME_OF[code] || code,
        price: e.price, chg_pct: Number((e.change_pct || 0).toFixed(2)),
        px_text: priceLabel(code, e.price, e.currency)
      });
    });
    if (!items.length) return Promise.reject(new Error("보드 캐시 없음"));
    return Promise.resolve(jsonResponse({ items: items, live: true }));
  }

  window.fetch = function (input, init) {
    try {
      var raw = typeof input === "string" ? input : input.url;
      var url = new URL(raw, window.location.origin);
      var path = url.pathname.replace(/^\/stockagent/, "");
      var fallback = function () { return prevFetch(input, init); };
      if (path === "/api/stocks/quote") return liveQuote(url.searchParams).catch(fallback);
      if (path === "/api/stocks/candles") return liveCandles(url.searchParams).catch(fallback);
      if (path === "/api/ticker_quotes" && document.getElementById("stockSvg")) {
        return liveTape().catch(fallback);
      }
    } catch (e) { /* URL 파싱 실패 등 → 원래 fetch로 */ }
    return prevFetch(input, init);
  };

  // ==== 이하 주식 페이지 전용 UI (다른 페이지에서는 아무 것도 하지 않음) ==========
  if (!document.getElementById("stockSvg")) return;

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  // ---- 입력창: 미국 티커/지수 심볼 허용 --------------------------------------------
  window.stockCode = function () {
    var el = document.getElementById("stock-code");
    var code = cleanCode(el ? el.value : "");
    if (el) el.value = code;
    return code;
  };
  var codeInput = document.getElementById("stock-code");
  if (codeInput) {
    codeInput.setAttribute("inputmode", "text");
    codeInput.placeholder = "005930 · AAPL · ^GSPC";
  }

  // ---- 시세 패널 렌더러 교체 (통화별 표기 + 실데이터 필드) ---------------------------
  window.renderStockQuote = function (q) {
    var change = Number(q.change || 0);
    var pct = q.change_pct == null ? null : Number(q.change_pct);
    var priceEl = document.getElementById("stock-price");
    if (priceEl) priceEl.textContent = q.price_text || (window.KRW ? KRW(q.price) + " 원" : q.price);
    var chEl = document.getElementById("stock-change");
    if (chEl) {
      chEl.textContent = (q.change_text || (window.KRW ? KRW(change, true) : change)) +
        (pct == null ? "" : " (" + (pct > 0 ? "+" : "") + pct.toFixed(2) + "%)");
      chEl.className = "val " + (change > 0 ? "up" : change < 0 ? "down" : "muted");
    }
    var stEl = document.getElementById("stock-status");
    if (stEl) {
      stEl.textContent = q.status || "—";
      stEl.className = "val " + (q.status === "장중" ? "up" : "");
    }
    var updEl = document.getElementById("stock-updated");
    if (updEl) updEl.textContent = q.updated_at || "—";
    var rowsEl = document.getElementById("stock-quote-rows");
    if (rowsEl) rowsEl.innerHTML = (q.rows || []).map(function (r) {
      return '<div class="quote-row"><span class="k">' + esc(r.label || "—") +
        '</span><span class="v">' + esc(r.value || "—") + "</span></div>";
    }).join("");
    var metEl = document.getElementById("stock-metric-rows");
    if (metEl) metEl.innerHTML = (q.metrics || []).map(function (r) {
      return '<div class="quote-row"><span class="k">' + esc(r.label || "—") +
        '</span><span class="v">' + esc(r.value || "—") + "</span></div>";
    }).join("");
    var flowEl = document.getElementById("stock-flow");
    if (flowEl) flowEl.textContent = q.flow_note || "—";
  };

  // ---- 티커 테이프 렌더러 교체 (통화별 표기 지원) -------------------------------------
  window.renderTickerTape = function (items) {
    var tape = document.getElementById("tape");
    if (!tape || !items || !items.length) return;
    var cell = function (it) {
      var col = it.chg_pct >= 0 ? "#1fd6a8" : "#ff5d6c";
      var sign = it.chg_pct >= 0 ? "+" : "";
      var px = it.px_text || (window.KRW ? KRW(it.price) : it.price);
      return '<span class="tape-cell"><span class="sym">' + esc(it.sym) +
        '</span><span class="px">' + esc(px) + '</span>' +
        '<span style="color:' + col + ';font-weight:600">' + sign + Number(it.chg_pct).toFixed(2) + "%</span></span>";
    };
    tape.innerHTML = items.map(cell).join("") + items.map(cell).join("");
  };

  // ---- 종목 프리셋: 한국/미국/지수 그룹 -----------------------------------------------
  (function buildPresets() {
    var sel = document.getElementById("stock-preset");
    if (!sel) return;
    var groups = [
      { label: "한국 주식", items: KR_STOCKS },
      { label: "미국 주식", items: US_STOCKS },
      { label: "지수 · 환율", items: INDICES }
    ];
    sel.innerHTML = groups.map(function (g) {
      return '<optgroup label="' + esc(g.label) + '">' + g.items.map(function (p) {
        return '<option value="' + esc(p[0]) + '">' + esc(p[1]) + " · " + esc(p[0]) + "</option>";
      }).join("") + "</optgroup>";
    }).join("");
    sel.value = "005930";
  })();

  // ---- 글로벌 마켓 보드 주입 -----------------------------------------------------------
  function sparkPath(closes, w, h) {
    var vals = (closes || []).filter(function (v) { return v != null; });
    if (vals.length < 2) return "";
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (hi === lo) { lo -= 1; hi += 1; }
    var d = "";
    for (var i = 0; i < vals.length; i++) {
      var x = (i / (vals.length - 1)) * w;
      var y = 4 + (h - 8) * (1 - (vals[i] - lo) / (hi - lo));
      d += (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1) + " ";
    }
    return d.trim();
  }

  function injectBoardSection() {
    var statGrid = document.querySelector(".market-stat-grid");
    if (!statGrid || document.getElementById("global-board")) return;
    var wrap = document.createElement("div");
    wrap.id = "global-board";
    var inner = '<div class="section-line" style="margin-top:2px">' +
      '<div class="section-title">글로벌 마켓 라이브</div>' +
      '<span class="coin-market-count" id="global-board-upd">불러오는 중…</span></div>';
    BOARD_GROUPS.forEach(function (g, gi) {
      inner += '<div class="coin-board-sub" style="margin:0 0 7px">' + esc(g.label) + "</div>" +
        '<div class="coin-market-board-grid" data-board-group="' + gi + '"></div>';
    });
    wrap.innerHTML = inner;
    statGrid.parentNode.insertBefore(wrap, statGrid.nextSibling);
    wrap.addEventListener("click", function (e) {
      var card = e.target.closest("[data-board-code]");
      if (!card) return;
      var input = document.getElementById("stock-code");
      if (input) input.value = card.getAttribute("data-board-code");
      if (typeof window.loadStock === "function") window.loadStock();
      var chart = document.getElementById("stockSvg");
      if (chart) chart.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  function renderBoard() {
    var updEl = document.getElementById("global-board-upd");
    if (updEl && boardUpdatedAt) {
      updEl.textContent = "갱신 " + boardUpdatedAt.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    BOARD_GROUPS.forEach(function (g, gi) {
      var grid = document.querySelector('[data-board-group="' + gi + '"]');
      if (!grid) return;
      grid.innerHTML = g.items.map(function (p) {
        var code = p[0], name = p[1], e = boardCache[code];
        if (!e) {
          return '<button class="coin-mini-card" data-board-code="' + esc(code) + '">' +
            '<div class="coin-mini-head"><div><div class="coin-mini-symbol">' + esc(name) + "</div>" +
            '<div class="coin-mini-name">' + esc(code) + '</div></div></div>' +
            '<div class="coin-mini-price muted">로딩…</div></button>';
        }
        var up = (e.change_pct || 0) >= 0;
        var col = up ? "#1fd6a8" : "#ff5d6c";
        var path = sparkPath(e.closes, 200, 58);
        return '<button class="coin-mini-card" data-board-code="' + esc(code) + '">' +
          '<div class="coin-mini-head"><div>' +
          '<div class="coin-mini-symbol">' + esc(name) + "</div>" +
          '<div class="coin-mini-name">' + esc(code) + "</div></div>" +
          '<div class="coin-mini-change ' + (up ? "up" : "down") + '">' + (up ? "+" : "") +
          Number(e.change_pct || 0).toFixed(2) + "%</div></div>" +
          '<div class="coin-mini-price">' + esc(priceLabel(code, e.price, e.currency)) + "</div>" +
          '<svg class="coin-mini-chart" viewBox="0 0 200 58" preserveAspectRatio="none">' +
          (path ? '<path d="' + path + '" fill="none" stroke="' + col + '" stroke-width="1.6" vector-effect="non-scaling-stroke"></path>' : "") +
          "</svg></button>";
      }).join("");
    });
  }

  // ---- 하단 안내 문구 -------------------------------------------------------------------
  var foot = document.getElementById("foot");
  if (foot) {
    foot.textContent = "실시간 시세: Yahoo Finance(한국 .KS/.KQ · 미국 · 지수 · 환율) · " +
      "CORS 프록시 경유 · 거래소별 최대 15~20분 지연 가능 · 뉴스: 실시간 RSS";
  }

  // ---- 시작: 보드 주입 + 즉시 실데이터 재조회 + 주기 갱신 -------------------------------
  injectBoardSection();
  renderBoard();
  refreshBoard(true).then(function () {
    if (typeof window.loadTickerTape === "function") window.loadTickerTape();
  });
  setInterval(function () { refreshBoard(false); }, BOARD_REFRESH_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshBoard(false);
  });
  // 페이지 로드시 목업으로 이미 그려졌으므로 실데이터로 한 번 더 그린다.
  if (typeof window.loadStock === "function") window.loadStock();
})();
