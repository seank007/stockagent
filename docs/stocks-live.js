/*
 * stocks-live.js — GitHub Pages(정적)에서 실시간 주식 시세를 붙이는 스크립트.
 *
 * 서버가 없으므로 Yahoo Finance 공개 chart/spark/search API를 CORS 프록시로
 * 브라우저에서 직접 호출한다. 한국(.KS/.KQ)·미국·유럽·홍콩·일본·지수·환율을
 * 지원하며, 기존 UI가 호출하는 /api/stocks/quote · /api/stocks/candles ·
 * /api/ticker_quotes 목업 응답을 실데이터로 대체한다.
 *
 * 속도: 프록시를 순차가 아니라 0.7초 간격 헤지 병렬로 레이스시키고, 성공한
 * 프록시·심볼 해석·마지막 보드 시세를 localStorage에 저장해 재방문 시 즉시
 * 그린 뒤 백그라운드로 갱신한다.
 *
 * UI: 첫 화면은 즐겨찾기(★ 토글, 저장됨)만 보여주고, HTS처럼 국가별 마켓
 * 탭(한국/미국/유럽/홍콩·중국/일본/지수·환율)과 전종목 실시간 검색을 제공한다.
 *
 * 이 스크립트는 정적 목업(fetch 오버라이드)·news-live.js·chart-hts.js "다음"에
 * 로드된다. 라이브 호출이 실패하면 목업 응답으로 폴백한다.
 */
(function () {
  "use strict";

  var prevFetch = window.fetch.bind(window);
  var nativeFetch = window.__stockagentNativeFetch || prevFetch;

  // ---- 공개 CORS 프록시 ------------------------------------------------------
  var PROXIES = [
    { build: function (u) { return "https://corsproxy.io/?url=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.allorigins.win/raw?url=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.codetabs.com/v1/proxy/?quest=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.allorigins.win/get?url=" + encodeURIComponent(u); }, wrapped: true },
    { build: function (u) { return "https://thingproxy.freeboard.io/fetch/" + u; } }
  ];
  var HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"];
  var TRY_TIMEOUT_MS = 7000;
  var HEDGE_DELAY_MS = 700;      // 앞 시도가 안 끝나면 0.7초 뒤 다음 프록시도 병렬 발사
  var TOTAL_DEADLINE_MS = 20000;
  var QUOTE_TTL = 2000;
  var HIST_TTL = 5 * 60000;
  var BOARD_REFRESH_MS = 10000;
  var DIRECT_TIMEOUT_MS = 1800;
  var DIRECT_PROBE_COOLDOWN_MS = 15000;
  var directApiBase = null;
  var nextDirectProbeAt = 0;

  function uniquePush(rows, value) {
    if (value == null) return;
    value = String(value).trim().replace(/\/+$/, "");
    if (!value && value !== "") return;
    if (rows.indexOf(value) === -1) rows.push(value);
  }

  function configuredApiBase() {
    try {
      var params = new URLSearchParams(location.search || "");
      var fromQuery = params.get("liveApi") || params.get("api");
      if (fromQuery) {
        localStorage.setItem("stockagentLiveApiBase", fromQuery);
        return fromQuery;
      }
    } catch (e) {}
    if (window.STOCKAGENT_LIVE_API_BASE) return window.STOCKAGENT_LIVE_API_BASE;
    try { return localStorage.getItem("stockagentLiveApiBase"); } catch (e) { return null; }
  }

  function directApiBases() {
    var bases = [];
    uniquePush(bases, configuredApiBase());
    if (!/\.github\.io$/.test(location.hostname) && /^https?:$/.test(location.protocol)) {
      uniquePush(bases, location.origin);
    }
    uniquePush(bases, "http://127.0.0.1:8000");
    uniquePush(bases, "http://localhost:8000");
    return bases;
  }

  function directUrl(base, pathQuery) {
    var sep = pathQuery.indexOf("?") === -1 ? "?" : "&";
    if (!base) return pathQuery + sep + "_live=" + Date.now();
    return base.replace(/\/+$/, "") + pathQuery + sep + "_live=" + Date.now();
  }

  function targetAddressSpace(url) {
    try {
      var host = new URL(url, location.href).hostname.toLowerCase();
      if (host === "localhost" || host === "::1" || /^127\./.test(host)) return "loopback";
      if (
        host.endsWith(".local")
        || /^10\./.test(host)
        || /^192\.168\./.test(host)
        || /^169\.254\./.test(host)
        || /^172\.(1[6-9]|2\d|3[01])\./.test(host)
      ) return "local";
    } catch (e) {}
    return null;
  }

  function directFetchJson(url) {
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, DIRECT_TIMEOUT_MS) : null;
    var init = { cache: "no-store", credentials: "omit", mode: "cors" };
    var target = targetAddressSpace(url);
    if (target) init.targetAddressSpace = target;
    if (ctrl) init.signal = ctrl.signal;
    return nativeFetch(url, init).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).finally(function () {
      if (timer) clearTimeout(timer);
    });
  }

  function xhrJson(url) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", url, true);
      xhr.timeout = DIRECT_TIMEOUT_MS;
      xhr.responseType = "json";
      xhr.onload = function () {
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error("HTTP " + xhr.status));
          return;
        }
        var data = xhr.response;
        if (data == null && xhr.responseText) {
          try { data = JSON.parse(xhr.responseText); } catch (e) {}
        }
        data == null ? reject(new Error("empty response")) : resolve(data);
      };
      xhr.onerror = function () { reject(new Error("network error")); };
      xhr.ontimeout = function () { reject(new Error("timeout")); };
      try { xhr.send(); } catch (e) { reject(e); }
    });
  }

  function directRequestJson(url) {
    return directFetchJson(url).catch(function () { return xhrJson(url); });
  }

  function readDirectApi(pathQuery, validate) {
    var bases = directApiBase ? [directApiBase] : directApiBases();
    if (!directApiBase && Date.now() < nextDirectProbeAt) {
      return Promise.reject(new Error("direct API probe cooling down"));
    }
    var idx = 0;
    function tryNext(lastErr) {
      if (idx >= bases.length) {
        directApiBase = null;
        nextDirectProbeAt = Date.now() + DIRECT_PROBE_COOLDOWN_MS;
        return Promise.reject(lastErr || new Error("direct API unavailable"));
      }
      var base = bases[idx++];
      return directRequestJson(directUrl(base, pathQuery)).then(function (data) {
        if (validate && !validate(data)) throw new Error("invalid direct payload");
        directApiBase = base;
        nextDirectProbeAt = 0;
        return data;
      }).catch(tryNext);
    }
    return tryNext();
  }

  function directResponse(pathQuery, validate) {
    return readDirectApi(pathQuery, validate).then(jsonResponse);
  }

  function pathWithSearch(path, url) {
    return path + (url.search || "");
  }

  // ---- localStorage ----------------------------------------------------------
  var LS = {
    get: function (k, fallback) {
      try { var v = localStorage.getItem("sa." + k); return v == null ? fallback : JSON.parse(v); }
      catch (e) { return fallback; }
    },
    set: function (k, v) {
      try { localStorage.setItem("sa." + k, JSON.stringify(v)); } catch (e) {}
    }
  };

  // ---- 마켓 사전 (탭별 대표 종목 — 그 외 전 종목은 검색으로) -------------------
  var KR_STOCKS = [
    ["005930", "삼성전자"], ["000660", "SK하이닉스"], ["373220", "LG에너지솔루션"],
    ["207940", "삼성바이오로직스"], ["005380", "현대차"], ["000270", "기아"],
    ["035420", "NAVER"], ["035720", "카카오"], ["005490", "POSCO홀딩스"],
    ["105560", "KB금융"], ["247540", "에코프로비엠"], ["196170", "알테오젠"]
  ];
  var US_STOCKS = [
    ["AAPL", "애플"], ["MSFT", "마이크로소프트"], ["NVDA", "엔비디아"],
    ["GOOGL", "알파벳"], ["AMZN", "아마존"], ["TSLA", "테슬라"],
    ["META", "메타"], ["AVGO", "브로드컴"], ["NFLX", "넷플릭스"],
    ["AMD", "AMD"], ["JPM", "JP모건"], ["BRK-B", "버크셔B"]
  ];
  var EU_STOCKS = [
    ["ASML.AS", "ASML"], ["SAP.DE", "SAP"], ["MC.PA", "LVMH"],
    ["NOVO-B.CO", "노보노디스크"], ["NESN.SW", "네슬레"], ["SIE.DE", "지멘스"],
    ["TTE.PA", "토탈에너지스"], ["AZN.L", "아스트라제네카"], ["SHEL.L", "쉘"],
    ["AIR.PA", "에어버스"], ["RACE.MI", "페라리"], ["HSBA.L", "HSBC(런던)"]
  ];
  var HK_STOCKS = [
    ["0700.HK", "텐센트"], ["9988.HK", "알리바바"], ["3690.HK", "메이투안"],
    ["1810.HK", "샤오미"], ["1299.HK", "AIA"], ["0941.HK", "차이나모바일"],
    ["2318.HK", "핑안보험"], ["9618.HK", "징둥닷컴"], ["1211.HK", "BYD"],
    ["0005.HK", "HSBC"], ["0388.HK", "홍콩거래소"], ["2020.HK", "안타스포츠"]
  ];
  var JP_STOCKS = [
    ["7203.T", "도요타"], ["6758.T", "소니"], ["9984.T", "소프트뱅크G"],
    ["6861.T", "키엔스"], ["8035.T", "도쿄일렉트론"], ["7974.T", "닌텐도"],
    ["9983.T", "패스트리테일링"], ["8306.T", "미쓰비시UFJ"], ["4063.T", "신에츠화학"],
    ["6501.T", "히타치"], ["7267.T", "혼다"], ["6902.T", "덴소"]
  ];
  var INDICES = [
    ["^KS11", "코스피"], ["^KQ11", "코스닥"], ["^GSPC", "S&P 500"],
    ["^IXIC", "나스닥"], ["^DJI", "다우존스"], ["^SOX", "필라델피아 반도체"],
    ["^N225", "닛케이 225"], ["^HSI", "항셍"], ["^GDAXI", "DAX"],
    ["^FTSE", "FTSE 100"], ["KRW=X", "원/달러"], ["EURKRW=X", "원/유로"]
  ];

  var NAME_OF = {};
  [KR_STOCKS, US_STOCKS, EU_STOCKS, HK_STOCKS, JP_STOCKS, INDICES].forEach(function (list) {
    list.forEach(function (p) { NAME_OF[p[0]] = p[1]; });
  });
  // 검색으로 추가한 종목 이름(localStorage 유지)
  var EXTRA_NAMES = LS.get("names", {});
  function nameOf(code) { return NAME_OF[code] || EXTRA_NAMES[code] || code; }
  function rememberName(code, name) {
    if (!name || NAME_OF[code] || EXTRA_NAMES[code]) return;
    EXTRA_NAMES[code] = name;
    LS.set("names", EXTRA_NAMES);
  }

  // ---- 마켓 탭 ----------------------------------------------------------------
  var DEFAULT_FAVS = ["005930", "000660", "AAPL", "NVDA", "TSLA", "^KS11", "^IXIC", "KRW=X"];
  function getFavs() {
    var favs = LS.get("favs", null);
    return Array.isArray(favs) && favs.length ? favs : DEFAULT_FAVS.slice();
  }
  function isFav(code) { return getFavs().indexOf(code) !== -1; }
  function toggleFav(code) {
    var favs = getFavs();
    var i = favs.indexOf(code);
    if (i === -1) favs.push(code); else favs.splice(i, 1);
    LS.set("favs", favs);
    return i === -1;
  }

  var MARKET_TABS = [
    { id: "favs", label: "★ 즐겨찾기", codes: getFavs },
    { id: "kr", label: "한국", codes: function () { return KR_STOCKS.map(function (p) { return p[0]; }); } },
    { id: "us", label: "미국", codes: function () { return US_STOCKS.map(function (p) { return p[0]; }); } },
    { id: "eu", label: "유럽", codes: function () { return EU_STOCKS.map(function (p) { return p[0]; }); } },
    { id: "hk", label: "홍콩·중국", codes: function () { return HK_STOCKS.map(function (p) { return p[0]; }); } },
    { id: "jp", label: "일본", codes: function () { return JP_STOCKS.map(function (p) { return p[0]; }); } },
    { id: "idx", label: "지수·환율", codes: function () { return INDICES.map(function (p) { return p[0]; }); } }
  ];
  var activeTab = LS.get("tab", "favs");
  if (!MARKET_TABS.some(function (t) { return t.id === activeTab; })) activeTab = "favs";

  // 테이프: 즐겨찾기 + 핵심 지수
  var TAPE_CORE = ["^KS11", "^GSPC", "^IXIC", "KRW=X"];
  function tapeCodes() {
    var codes = getFavs().slice();
    TAPE_CORE.forEach(function (c) { if (codes.indexOf(c) === -1) codes.push(c); });
    return codes.slice(0, 14);
  }

  // ---- HTTP: 헤지 병렬 레이스 ---------------------------------------------------
  function fetchWithTimeout(url, wrapped, timeoutMs) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, timeoutMs || TRY_TIMEOUT_MS);
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

  function looksLikeSparkMap(json) {
    if (!json || typeof json !== "object" || Array.isArray(json)) return false;
    var keys = Object.keys(json);
    if (!keys.length) return false;
    var v = json[keys[0]];
    return !!(v && typeof v === "object" && Array.isArray(v.close));
  }

  function validYahoo(json) {
    return !!(json && (json.chart || json.spark || json.quotes || json.finance || looksLikeSparkMap(json)));
  }

  var goodProxyIdx = Number(LS.get("proxy", 0)) || 0;

  // 성공 프록시 우선 + 0.7초 간격 헤지 병렬. 최초 성공이 이긴다.
  function fetchYahoo(pathQuery) {
    var attempts = [];
    PROXIES.forEach(function (proxy, pi) {
      HOSTS.forEach(function (host) {
        var url = host + pathQuery + (pathQuery.indexOf("?") === -1 ? "?" : "&") +
          "ts=" + Math.floor(Date.now() / 30000); // 프록시측 캐시 회피(30초 단위)
        attempts.push({ proxy: proxy, proxyIdx: pi, url: url });
      });
    });
    attempts.sort(function (a, b) {
      return (a.proxyIdx === goodProxyIdx ? 0 : 1) - (b.proxyIdx === goodProxyIdx ? 0 : 1);
    });
    return new Promise(function (resolve, reject) {
      var settled = false, next = 0, failed = 0, timers = [];
      var deadline = setTimeout(function () { fail(new Error("deadline")); }, TOTAL_DEADLINE_MS);
      function done(json, proxyIdx) {
        if (settled) return;
        settled = true;
        clearTimeout(deadline);
        timers.forEach(clearTimeout);
        goodProxyIdx = proxyIdx;
        LS.set("proxy", proxyIdx);
        resolve(json);
      }
      function fail(err) {
        if (settled) return;
        settled = true;
        clearTimeout(deadline);
        timers.forEach(clearTimeout);
        reject(err);
      }
      function launchNext() {
        if (settled || next >= attempts.length) return;
        var a = attempts[next++];
        fetchWithTimeout(a.proxy.build(a.url), a.proxy.wrapped)
          .then(function (json) {
            if (!validYahoo(json)) throw new Error("야후 응답 아님");
            done(json, a.proxyIdx);
          })
          .catch(function () {
            failed++;
            if (failed >= attempts.length) fail(new Error("모든 프록시 실패"));
            else launchNext(); // 실패 즉시 다음 시도
          });
      }
      launchNext();
      for (var k = 1; k < attempts.length; k++) {
        timers.push(setTimeout(launchNext, HEDGE_DELAY_MS * k));
      }
    });
  }

  // ---- 차트 캐시 + 심볼 해석 ------------------------------------------------------
  var chartCache = {};
  var RESOLVED = LS.get("resolved", {}); // "005930" → "005930.KS" 등

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
        .then(function (result) {
          if (RESOLVED[code] !== sym) { RESOLVED[code] = sym; LS.set("resolved", RESOLVED); }
          return { symbol: sym, result: result };
        })
        .catch(function (e) { if (idx < candidates.length) return attempt(); throw e; });
    }
    return attempt();
  }

  // ---- 지표 계산 ------------------------------------------------------------------
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

  // ---- 표시 형식 --------------------------------------------------------------------
  function fmtNum(v, digits) {
    if (v == null || isNaN(v)) return "—";
    return Number(v).toLocaleString("ko-KR", {
      maximumFractionDigits: digits,
      minimumFractionDigits: digits > 0 && Math.abs(v) < 10000 ? Math.min(digits, 2) : 0
    });
  }

  var CUR_SIGN = { USD: "$", EUR: "€", GBP: "£", HKD: "HK$", CNY: "¥", TWD: "NT$" };

  function priceLabel(code, v, currency, signed) {
    if (v == null || isNaN(v)) return "—";
    var sign = signed && v > 0 ? "+" : "";
    if (code && code.charAt(0) === "^") return sign + fmtNum(v, 2);
    if (currency === "KRW") return sign + fmtNum(v, Math.abs(v) < 10000 ? 2 : 0) + " 원";
    if (currency === "JPY") return sign + fmtNum(v, 0) + " 엔";
    if (currency === "GBp") return sign + fmtNum(v, 2) + "p"; // 런던: 펜스 단위
    if (currency === "DKK" || currency === "SEK" || currency === "NOK") return sign + fmtNum(v, 2) + " kr";
    if (currency === "CHF") return sign + "CHF " + fmtNum(v, 2);
    if (CUR_SIGN[currency]) return sign + CUR_SIGN[currency] + fmtNum(v, 2);
    return sign + fmtNum(v, 2) + (currency ? " " + currency : "");
  }

  function currencyHint(code) {
    if (/^\d{6}$/.test(code) || /\.(KS|KQ)$/.test(code) || code === "KRW=X" || code === "EURKRW=X") return "KRW";
    if (code.charAt(0) === "^") return "";
    if (/\.HK$/.test(code)) return "HKD";
    if (/\.T$/.test(code)) return "JPY";
    if (/\.CO$/.test(code)) return "DKK";
    if (/\.(ST|SS)?ST$/.test(code)) return "SEK";
    if (/\.SW$/.test(code)) return "CHF";
    if (/\.(DE|PA|AS|MI|MC|BR|HE|IR|VI)$/.test(code)) return "EUR";
    if (/\.L$/.test(code)) return "GBp";
    return "USD";
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

  // ---- /api/stocks/quote ---------------------------------------------------------
  function buildQuote(code, symbol, r) {
    var meta = r.meta;
    var q0 = (r.indicators && r.indicators.quote && r.indicators.quote[0]) || {};
    var price = meta.regularMarketPrice;
    var prev = meta.chartPreviousClose != null ? meta.chartPreviousClose : meta.previousClose;
    var change = prev != null ? price - prev : 0;
    var pct = prev ? (change / prev) * 100 : null;
    var cur = meta.currency || currencyHint(code);
    var open = firstNonNull(q0.open);
    var status = marketStatus(meta);
    var updated = meta.regularMarketTime
      ? new Date(meta.regularMarketTime * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
      : "—";
    rememberName(code, meta.shortName || meta.longName);
    return {
      code: code, symbol: symbol, name: nameOf(code),
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

  // ---- /api/stocks/candles --------------------------------------------------------
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

  // ---- 보드 데이터 (spark 배치, 요청당 심볼 20개 제한 → 18개씩) ----------------------
  var boardCache = LS.get("board", {}); // code → {price, change_pct, closes, currency, t}
  var boardUpdatedAt = null;
  var boardLoading = false;

  function yahooSymbolFor(code) {
    if (RESOLVED[code]) return RESOLVED[code];
    if (/^\d{6}$/.test(code)) return code + ".KS";
    return code;
  }

  function codeOfSymbol(sym) {
    var m = String(sym).match(/^(\d{6})\.(KS|KQ)$/);
    return m ? m[1] : sym;
  }

  function storeBoardEntry(code, meta, closes) {
    var clean = (closes || []).filter(function (v) { return v != null; });
    var price = meta.regularMarketPrice != null ? meta.regularMarketPrice : clean[clean.length - 1];
    var prev = meta.chartPreviousClose != null ? meta.chartPreviousClose : meta.previousClose;
    if (price == null) return;
    boardCache[code] = {
      price: price,
      change_pct: prev ? ((price - prev) / prev) * 100 : 0,
      closes: clean.length > 48 ? clean.filter(function (_, i) { return i % Math.ceil(clean.length / 48) === 0; }).concat([clean[clean.length - 1]]) : clean,
      currency: meta.currency || currencyHint(code),
      t: Date.now()
    };
  }

  function sparkChunk(symbols) {
    var path = "/v8/finance/spark?symbols=" + encodeURIComponent(symbols.join(",")) +
      "&range=1d&interval=15m";
    return fetchYahoo(path).then(function (json) {
      var stored = 0;
      var results = (json.spark && json.spark.result) || [];
      if (results.length) {
        results.forEach(function (item) {
          var resp = item.response && item.response[0];
          if (!resp || !resp.meta) return;
          var closes = resp.indicators && resp.indicators.quote && resp.indicators.quote[0]
            ? resp.indicators.quote[0].close : [];
          storeBoardEntry(codeOfSymbol(item.symbol), resp.meta, closes);
          stored++;
        });
      } else if (looksLikeSparkMap(json)) {
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

  function refreshCodes(codes) {
    var symbols = [];
    codes.forEach(function (c) {
      var s = yahooSymbolFor(c);
      if (symbols.indexOf(s) === -1) symbols.push(s);
    });
    var chunks = [];
    for (var i = 0; i < symbols.length; i += 18) chunks.push(symbols.slice(i, i + 18));
    return Promise.all(chunks.map(sparkChunk)).then(function () {
      // spark가 빠뜨린 심볼(예: .KQ 종목을 .KS로 요청)만 개별 폴백 (최대 6개)
      var missing = codes.filter(function (c) { return !boardCache[c]; }).slice(0, 6);
      return Promise.all(missing.map(function (code) {
        return resolveChart(code, "1d", "15m", QUOTE_TTL).then(function (r) {
          var q0 = (r.result.indicators && r.result.indicators.quote && r.result.indicators.quote[0]) || {};
          storeBoardEntry(code, r.result.meta, q0.close || []);
        }).catch(function () {});
      }));
    });
  }

  function refreshBoard(force) {
    if (boardLoading || (document.hidden && !force)) return Promise.resolve(false);
    boardLoading = true;
    var tab = MARKET_TABS.filter(function (t) { return t.id === activeTab; })[0] || MARKET_TABS[0];
    var codes = tab.codes().slice();
    tapeCodes().forEach(function (c) { if (codes.indexOf(c) === -1) codes.push(c); });
    pfCodes().forEach(function (c) { if (codes.indexOf(c) === -1) codes.push(c); });  // 내 포트폴리오 종목 시세도 함께
    alertCodes().forEach(function (c) { if (codes.indexOf(c) === -1) codes.push(c); });  // 가격 알림 대상 시세도 함께
    return refreshCodes(codes)
      .then(function () {
        boardUpdatedAt = new Date();
        LS.set("board", boardCache);
        renderBoard();
        renderPortfolio();
        checkAlerts();
        renderAlerts();
        if (window.__syncSecNav) window.__syncSecNav();
        if (typeof window.loadTickerTape === "function" && !document.hidden) window.loadTickerTape();
        return true;
      })
      .catch(function () { return false; })
      .then(function (v) { boardLoading = false; return v; });
  }

  // ---- fetch 오버라이드 ---------------------------------------------------------------
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
    tapeCodes().forEach(function (code) {
      var e = boardCache[code];
      if (!e) return;
      items.push({
        sym: nameOf(code),
        price: e.price, chg_pct: Number((e.change_pct || 0).toFixed(2)),
        px_text: priceLabel(code, e.price, e.currency)
      });
    });
    if (!items.length) return Promise.reject(new Error("보드 캐시 없음"));
    return Promise.resolve(jsonResponse({ items: items, live: true }));
  }

  // ---- 네이버 금융 프록시(기업 분석용): 임의 URL을 CORS 프록시로 레이스 -----------------
  // 네이버는 프록시를 통과하면 느려서(재무 응답은 특히) per-try 타임아웃을 넉넉히 준다.
  var NAVER_TRY_MS = 13000;
  var NAVER_DEADLINE_MS = 30000;

  function fetchProxied(targetUrl, validate) {
    var attempts = PROXIES.map(function (p, pi) { return { proxy: p, proxyIdx: pi }; });
    attempts.sort(function (a, b) {
      return (a.proxyIdx === goodProxyIdx ? 0 : 1) - (b.proxyIdx === goodProxyIdx ? 0 : 1);
    });
    return new Promise(function (resolve, reject) {
      var settled = false, next = 0, failed = 0, timers = [];
      var deadline = setTimeout(function () { fail(new Error("deadline")); }, NAVER_DEADLINE_MS);
      function done(json, pi) {
        if (settled) return; settled = true;
        clearTimeout(deadline); timers.forEach(clearTimeout);
        goodProxyIdx = pi; LS.set("proxy", pi); resolve(json);
      }
      function fail(err) {
        if (settled) return; settled = true;
        clearTimeout(deadline); timers.forEach(clearTimeout); reject(err);
      }
      function launchNext() {
        if (settled || next >= attempts.length) return;
        var a = attempts[next++];
        fetchWithTimeout(a.proxy.build(targetUrl), a.proxy.wrapped, NAVER_TRY_MS)
          .then(function (json) {
            if (validate && !validate(json)) throw new Error("응답 형식 불일치");
            done(json, a.proxyIdx);
          })
          .catch(function () {
            failed++;
            if (failed >= attempts.length) fail(new Error("모든 프록시 실패"));
            else launchNext();
          });
      }
      launchNext();
      // 재무는 무거워서 헤지 간격을 조금 넓게(1.4초) — 프록시 동시 과부하 방지
      for (var k = 1; k < attempts.length; k++) timers.push(setTimeout(launchNext, 1400 * k));
    });
  }

  function naverApi(code, ep) {
    return fetchProxied("https://m.stock.naver.com/api/stock/" + code + "/" + ep,
      function (j) { return j && typeof j === "object"; });
  }

  function num(v) {
    if (v == null) return null;
    var t = String(v).replace(/[,%원배\s]/g, "");
    var n = parseFloat(t);
    return isNaN(n) ? null : n;
  }

  function financeTable(financeInfo) {
    var titles = (financeInfo && financeInfo.trTitleList) || [];
    var periods = titles.map(function (t) {
      return { key: t.key, label: t.title, estimate: t.isConsensus === "Y" };
    });
    var rows = ((financeInfo && financeInfo.rowList) || []).map(function (row) {
      var cols = row.columns || {};
      return {
        title: row.title,
        values: periods.map(function (p) { return (cols[p.key] || {}).value || "—"; })
      };
    });
    return { periods: periods, rows: rows };
  }

  var analysisCache = {};
  var EMPTY_FIN = { periods: [], rows: [] };

  // 재무제표는 프록시 통과가 느려서 별도로 받아 늦게 채운다. 완료되면 화면 패치.
  function fetchFinancials(code, base) {
    Promise.all([
      naverApi(code, "finance/annual").catch(function () { return null; }),
      naverApi(code, "finance/quarter").catch(function () { return null; })
    ]).then(function (fin) {
      base.annual = financeTable(fin[0] && fin[0].financeInfo);
      base.quarter = financeTable(fin[1] && fin[1].financeInfo);
      base.financials_pending = false;
      analysisCache[code] = { t: Date.now(), data: base };
      // 사용자가 아직 같은 종목을 보고 있으면 재무 표만 다시 그린다.
      if (window._analysisData && window._analysisData.code === code &&
          typeof window.renderFinancials === "function") {
        window._analysisData.annual = base.annual;
        window._analysisData.quarter = base.quarter;
        window._analysisData.financials_pending = false;
        window.renderFinancials(window._analysisData);
      }
    });
  }

  function liveAnalysis(code) {
    // 배포 스냅샷에 구워진 관심종목 분석이 있으면 프록시 없이 즉시 사용(안정).
    var baked = window.__stockAiSnapshot && window.__stockAiSnapshot.analysis;
    if (baked && baked[code] && baked[code].annual) {
      return Promise.resolve(jsonResponse(baked[code]));
    }
    var cached = analysisCache[code];
    if (cached && cached.data.annual.rows.length && Date.now() - cached.t < 10 * 60000) {
      return Promise.resolve(jsonResponse(cached.data));
    }
    return naverApi(code, "integration").then(function (integ) {
      var industry = ((integ.industryCompareInfo || []).slice(0, 6)).map(function (r) {
        var tmr = r.threeMonthEarningRate;
        return {
          code: r.itemCode, name: r.stockName, price: r.closePrice,
          change_pct: num(r.fluctuationsRatio), market_value: r.marketValue,
          three_month_return: (tmr == null || tmr === "N/A" || tmr === "-") ? null : num(tmr)
        };
      });
      var c = integ.consensusInfo || {};
      var researches = ((integ.researches || []).slice(0, 8)).map(function (r) {
        return { title: r.tit, broker: r.bnm, date: r.wdt, views: r.rcnt };
      });
      var data = {
        code: code, name: integ.stockName || code, industry_code: integ.industryCode,
        annual: EMPTY_FIN, quarter: EMPTY_FIN,
        industry_compare: industry,
        consensus: { target_price: c.priceTargetMean, recommend: c.recommMean, as_of: c.createDate },
        researches: researches, financials_pending: true
      };
      analysisCache[code] = { t: Date.now(), data: data };
      fetchFinancials(code, data);            // 백그라운드로 재무 채우기
      return jsonResponse(data);              // 나머지는 즉시 표시
    });
  }

  window.fetch = function (input, init) {
    try {
      var raw = typeof input === "string" ? input : input.url;
      var url = new URL(raw, window.location.origin);
      var path = url.pathname.replace(/^\/stockagent/, "");
      var fallback = function () { return prevFetch(input, init); };
      if (path === "/api/stocks/quote") {
        return directResponse(pathWithSearch(path, url), function (d) {
          return d && d.code && d.price != null;
        }).catch(function () { return liveQuote(url.searchParams).catch(fallback); });
      }
      if (path === "/api/stocks/ai") {
        return directResponse(pathWithSearch(path, url), function (d) {
          return d && Array.isArray(d.holdings);
        }).catch(fallback);
      }
      if (path === "/api/stocks/watchlist") {
        return directResponse(pathWithSearch(path, url), function (d) {
          return d && Array.isArray(d.items);
        }).catch(fallback);
      }
      if (path === "/api/stocks/candles") return liveCandles(url.searchParams).catch(fallback);
      if (path === "/api/stocks/analysis") {
        var code = (url.searchParams.get("code") || "005930").replace(/\D/g, "").slice(0, 6);
        return liveAnalysis(code).catch(fallback);
      }
      if (path === "/api/ticker_quotes" && document.getElementById("stockSvg")) {
        return directResponse(pathWithSearch(path, url), function (d) {
          return d && Array.isArray(d.items);
        }).catch(function () { return liveTape().catch(fallback); });
      }
    } catch (e) { /* URL 파싱 실패 등 → 원래 fetch로 */ }
    return prevFetch(input, init);
  };

  // ==== 이하 주식 페이지 전용 UI ========================================================
  if (!document.getElementById("stockSvg")) return;

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  // ---- 추가 스타일 ----------------------------------------------------------------------
  (function injectStyle() {
    var st = document.createElement("style");
    st.textContent =
      ".sa-star{cursor:pointer;color:#3a4658;font-size:13px;line-height:1;padding:1px 4px;user-select:none}" +
      ".sa-star.on{color:#e0b341}" +
      ".sa-star:hover{color:#f0c85e}" +
      ".sa-search-wrap{position:relative}" +
      ".sa-search-results{position:absolute;top:calc(100% + 5px);right:0;z-index:40;width:360px;max-height:330px;" +
      "overflow:auto;background:#0d1219;border:1px solid #2a3442;border-radius:5px;box-shadow:0 10px 30px rgba(0,0,0,.55)}" +
      ".sa-search-row{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;" +
      "padding:9px 11px;border-bottom:1px solid #11161f;cursor:pointer;font-size:11.5px}" +
      ".sa-search-row:last-child{border-bottom:none}" +
      ".sa-search-row:hover{background:#101720}" +
      ".sa-search-row .nm{color:#e6ebf2;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
      ".sa-search-row .sub{color:#5a6577;font-size:10px;margin-top:2px}" +
      ".sa-search-row .ex{color:#8a95a8;font-size:10px;white-space:nowrap}" +
      ".sa-empty-note{padding:14px;color:#5a6577;font-size:11px}";
    document.head.appendChild(st);
  })();

  // ---- 입력창: 글로벌 심볼 허용 -----------------------------------------------------------
  window.stockCode = function () {
    var el = document.getElementById("stock-code");
    var code = cleanCode(el ? el.value : "");
    if (el) el.value = code;
    return code;
  };
  var codeInput = document.getElementById("stock-code");
  if (codeInput) {
    codeInput.setAttribute("inputmode", "text");
    codeInput.placeholder = "005930 · AAPL · 0700.HK";
  }

  // ---- 시세 패널 렌더러 교체 ---------------------------------------------------------------
  window.renderStockQuote = function (q) {
    var change = Number(q.change || 0);
    var pct = q.change_pct == null ? null : Number(q.change_pct);
    var titleEl = document.getElementById("stock-title");
    if (titleEl && (q.name || q.code)) {
      titleEl.textContent = (q.name || q.code) + (q.code ? " · " + q.code : "");
    }
    var priceEl = document.getElementById("stock-price");
    if (priceEl) {
      priceEl.textContent = q.price_text || (window.KRW ? KRW(q.price) + " 원" : q.price);
      priceEl.className = change > 0 ? "up" : change < 0 ? "down" : "muted";
    }
    var chEl = document.getElementById("stock-change");
    if (chEl) {
      chEl.textContent = (q.change_text || (window.KRW ? KRW(change, true) : change)) +
        (pct == null ? "" : " (" + (pct > 0 ? "+" : "") + pct.toFixed(2) + "%)");
      chEl.className = change > 0 ? "up" : change < 0 ? "down" : "muted";
    }
    var stEl = document.getElementById("stock-status");
    if (stEl) {
      stEl.textContent = q.status || "—";
      stEl.className = "val " + (q.status === "장중" ? "up" : "");
    }
    var updEl = document.getElementById("stock-updated");
    if (updEl) {
      var asOf = q.generated_at ? new Date(q.generated_at).toLocaleTimeString("en-GB", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
      }) : (q.updated_at || "—");
      updEl.textContent = (q.source ? q.source + " · " : "") + asOf;
    }
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

  // ---- 티커 테이프 렌더러 교체 ---------------------------------------------------------------
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

  // ---- 종목 프리셋(우상단 select): 마켓별 그룹 -------------------------------------------------
  (function buildPresets() {
    var sel = document.getElementById("stock-preset");
    if (!sel) return;
    var groups = [
      { label: "한국", items: KR_STOCKS }, { label: "미국", items: US_STOCKS },
      { label: "유럽", items: EU_STOCKS }, { label: "홍콩·중국", items: HK_STOCKS },
      { label: "일본", items: JP_STOCKS }, { label: "지수·환율", items: INDICES }
    ];
    sel.innerHTML = groups.map(function (g) {
      return '<optgroup label="' + esc(g.label) + '">' + g.items.map(function (p) {
        return '<option value="' + esc(p[0]) + '">' + esc(p[1]) + " · " + esc(p[0]) + "</option>";
      }).join("") + "</optgroup>";
    }).join("");
    sel.value = "005930";
  })();

  // ---- 마켓 보드 (탭 + 검색 + 즐겨찾기) --------------------------------------------------------
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

  // ==== 내 주식 포트폴리오 =========================================================================
  // 보유 종목을 localStorage(sa.portfolio)에 { code: {qty, avg} } 로 저장한다. 시세는 boardCache를
  // 재사용하고, "KIS 계좌 연결" 버튼으로 로컬 봇의 /api/stocks/portfolio(실계좌/모의/페이퍼)에서
  // 자동으로 불러올 수도 있다.
  var PF = LS.get("portfolio", {});

  function pfCodes() { return Object.keys(PF); }
  function savePF() { LS.set("portfolio", PF); }

  function downloadCSV(filename, rows) {
    var csv = rows.map(function (r) {
      return r.map(function (c) { var s = String(c == null ? "" : c); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }).join(",");
    }).join("\n");
    var blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 100);
  }

  function pfNumber(n, digits) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits == null ? 0 : digits });
  }

  function isKrCode(code) { return /^\d{6}$/.test(code); }

  function injectPortfolioSection(beforeEl) {
    if (document.getElementById("stock-portfolio")) return;
    var st = document.createElement("style");
    st.textContent =
      "#stock-portfolio{margin:6px 0 14px}" +
      "#pf-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:8px 0}" +
      "#pf-summary .cell{background:#0d1219;border:1px solid #1c2430;border-radius:6px;padding:9px 12px}" +
      "#pf-summary .label{font-size:10.5px;color:#5a6577}" +
      "#pf-summary .val{font-size:15px;font-weight:700;margin-top:3px}" +
      ".pf-table{width:100%;border-collapse:collapse;font-size:12px}" +
      ".pf-table th,.pf-table td{padding:7px 9px;border-bottom:1px solid #141a23;text-align:right;white-space:nowrap}" +
      ".pf-table th{color:#5a6577;font-weight:600;font-size:10.5px;text-align:right}" +
      ".pf-table th:first-child,.pf-table td:first-child{text-align:left}" +
      ".pf-table tr:hover td{background:#101720}" +
      ".pf-name{color:#e6ebf2;font-weight:700}.pf-code{color:#5a6577;font-size:10px}" +
      ".pf-del{cursor:pointer;color:#5a6577;border:none;background:none;font-size:14px}" +
      ".pf-del:hover{color:#ff5d6c}" +
      ".pf-row-btn{cursor:pointer}" +
      "#pf-addform{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0;padding:10px;" +
      "background:#0a0e14;border:1px solid #1c2430;border-radius:6px}" +
      "#pf-addform input{font-family:inherit;font-size:12px;background:#0d1219;border:1px solid #1c2430;" +
      "color:#e6ebf2;border-radius:4px;padding:6px 8px;width:110px}" +
      ".pf-up{color:#1fd6a8}.pf-down{color:#ff5d6c}" +
      ".pf-empty{color:#5a6577;font-size:12px;padding:14px 4px}";
    document.head.appendChild(st);

    var wrap = document.createElement("div");
    wrap.id = "stock-portfolio";
    wrap.innerHTML =
      '<div class="section-line" style="flex-wrap:wrap;gap:10px">' +
      '  <div class="section-title" style="margin:0">내 주식 포트폴리오</div>' +
      '  <div class="inline-tools">' +
      '    <button class="mini-btn" id="pf-connect" title="로컬 봇의 KIS/페이퍼 계좌에서 보유 종목 불러오기">KIS 계좌 연결</button>' +
      '    <button class="mini-btn" id="pf-csv" title="CSV로 내보내기">CSV</button>' +
      '    <button class="mini-btn on" id="pf-add-toggle">+ 종목 추가</button>' +
      "  </div>" +
      "</div>" +
      '<div id="pf-msg" style="font-size:11px;color:#5a6577;margin:2px 0"></div>' +
      '<div id="pf-addform" hidden>' +
      '  <input id="pf-in-code" placeholder="종목코드 (예: 005930)" autocomplete="off">' +
      '  <input id="pf-in-qty" type="number" min="0" step="1" placeholder="수량">' +
      '  <input id="pf-in-avg" type="number" min="0" step="0.01" placeholder="평균단가">' +
      '  <button class="mini-btn on" id="pf-save">저장</button>' +
      '  <button class="mini-btn" id="pf-cancel">취소</button>' +
      "</div>" +
      '<div id="pf-summary"></div>' +
      '<div id="pf-table"></div>';
    if (beforeEl && beforeEl.parentNode) beforeEl.parentNode.insertBefore(wrap, beforeEl);
    else document.body.appendChild(wrap);

    var form = wrap.querySelector("#pf-addform");
    var codeIn = wrap.querySelector("#pf-in-code");
    var qtyIn = wrap.querySelector("#pf-in-qty");
    var avgIn = wrap.querySelector("#pf-in-avg");

    function openForm(code) {
      form.hidden = false;
      if (code) {
        codeIn.value = code;
        qtyIn.value = PF[code] ? PF[code].qty : "";
        avgIn.value = PF[code] ? PF[code].avg : "";
      }
      (code ? qtyIn : codeIn).focus();
    }
    function closeForm() { form.hidden = true; codeIn.value = qtyIn.value = avgIn.value = ""; }

    wrap.querySelector("#pf-add-toggle").addEventListener("click", function () {
      if (form.hidden) openForm(); else closeForm();
    });
    wrap.querySelector("#pf-cancel").addEventListener("click", closeForm);
    wrap.querySelector("#pf-save").addEventListener("click", function () {
      var code = cleanCode(codeIn.value);
      var qty = parseFloat(qtyIn.value);
      var avg = parseFloat(avgIn.value);
      if (!code || !isFinite(qty) || qty <= 0) { setPfMsg("종목코드와 수량을 정확히 입력하세요.", true); return; }
      PF[code] = { qty: qty, avg: isFinite(avg) && avg > 0 ? avg : (PF[code] ? PF[code].avg : 0) };
      savePF();
      closeForm();
      setPfMsg("");
      renderPortfolio();
      refreshBoard(true);   // 새 종목 시세 즉시 로드
    });
    codeIn.addEventListener("keydown", function (e) { if (e.key === "Enter") qtyIn.focus(); });
    avgIn.addEventListener("keydown", function (e) { if (e.key === "Enter") wrap.querySelector("#pf-save").click(); });

    // 행 클릭(편집) / 삭제
    wrap.querySelector("#pf-table").addEventListener("click", function (e) {
      var del = e.target.closest("[data-pf-del]");
      if (del) {
        e.stopPropagation();
        delete PF[del.getAttribute("data-pf-del")];
        savePF();
        renderPortfolio();
        return;
      }
      var row = e.target.closest("[data-pf-edit]");
      if (row) openForm(row.getAttribute("data-pf-edit"));
    });

    wrap.querySelector("#pf-connect").addEventListener("click", connectAccount);
    wrap.querySelector("#pf-csv").addEventListener("click", function () {
      var rows = [["종목코드", "종목명", "수량", "평균단가", "현재가", "평가금액", "평가손익", "수익률(%)"]];
      pfCodes().forEach(function (code) {
        var pos = PF[code], e = boardCache[code], cur = e ? e.price : "", name = nameOf(code);
        var evalAmt = cur !== "" ? cur * pos.qty : "", cost = pos.avg > 0 ? pos.avg * pos.qty : "";
        var pnl = (evalAmt !== "" && cost !== "") ? evalAmt - cost : "", pct = (pnl !== "" && cost > 0) ? (pnl / cost * 100).toFixed(2) : "";
        rows.push([code, name, pos.qty, pos.avg, cur, evalAmt, pnl, pct]);
      });
      downloadCSV("portfolio_" + new Date().toISOString().slice(0, 10) + ".csv", rows);
    });
    wrap.__openForm = openForm;
  }

  function setPfMsg(text, warn) {
    var el = document.getElementById("pf-msg");
    if (!el) return;
    el.textContent = text || "";
    el.style.color = warn ? "#e0b341" : "#5a6577";
  }

  function connectAccount() {
    setPfMsg("계좌 조회 중…");
    fetch("/api/stocks/portfolio", { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var holdings = (j && j.holdings) || [];
        if (!j || j.ok === false) { setPfMsg("연결 실패: " + ((j && j.error) || "로컬 봇에서만 가능합니다"), true); return; }
        if (!holdings.length) { setPfMsg("계좌에 보유 종목이 없습니다. (출처: " + (j.source || "-") + ")", true); return; }
        var n = 0;
        holdings.forEach(function (h) {
          var code = cleanCode(h.code);
          if (!code || !(h.qty > 0)) return;
          PF[code] = { qty: Number(h.qty), avg: Number(h.avg_price) || 0 };
          if (h.name) rememberName(code, h.name);
          n++;
        });
        savePF();
        setPfMsg(n + "개 종목을 불러왔습니다. (출처: " + (j.source || "-") + ")");
        renderPortfolio();
        refreshBoard(true);
      })
      .catch(function () {
        setPfMsg("연결 실패 — GitHub Pages에서는 계좌 연결이 불가하고, 로컬 봇 실행 중일 때만 됩니다.", true);
      });
  }

  function renderPortfolio() {
    var table = document.getElementById("pf-table");
    var summary = document.getElementById("pf-summary");
    if (!table || !summary) return;
    var codes = pfCodes();
    if (!codes.length) {
      summary.innerHTML = "";
      table.innerHTML = '<div class="pf-empty">보유 종목이 없습니다. "+ 종목 추가"로 직접 입력하거나 "KIS 계좌 연결"로 자동으로 불러오세요.</div>';
      return;
    }
    var totalEval = 0, totalCost = 0, priced = 0;
    var rows = codes.map(function (code) {
      var pos = PF[code];
      var e = boardCache[code];
      var name = nameOf(code);
      var cur = e ? e.price : null;
      var evalAmt = cur != null ? cur * pos.qty : null;
      var cost = pos.avg > 0 ? pos.avg * pos.qty : null;
      if (evalAmt != null) { totalEval += evalAmt; if (cost != null) { totalCost += cost; priced++; } }
      var pnl = (evalAmt != null && cost != null) ? evalAmt - cost : null;
      var pct = (pnl != null && cost > 0) ? (pnl / cost) * 100 : null;
      var cls = pnl == null ? "" : (pnl >= 0 ? "pf-up" : "pf-down");
      var sign = pnl != null && pnl > 0 ? "+" : "";
      return "<tr>" +
        '<td class="pf-row-btn" data-pf-edit="' + esc(code) + '">' +
        '<div class="pf-name">' + esc(name) + '</div><div class="pf-code">' + esc(code) + "</div></td>" +
        "<td>" + pfNumber(pos.qty, 4) + "</td>" +
        "<td>" + (pos.avg > 0 ? pfNumber(pos.avg, 2) : "—") + "</td>" +
        "<td>" + (cur != null ? pfNumber(cur, 2) : "…") + "</td>" +
        "<td>" + (evalAmt != null ? pfNumber(evalAmt) : "…") + "</td>" +
        '<td class="' + cls + '">' + (pnl != null ? sign + pfNumber(pnl) : "—") + "</td>" +
        '<td class="' + cls + '">' + (pct != null ? sign + pct.toFixed(2) + "%" : "—") + "</td>" +
        '<td><button class="pf-del" data-pf-del="' + esc(code) + '" title="삭제">×</button></td>' +
        "</tr>";
    }).join("");
    table.innerHTML =
      '<table class="pf-table"><thead><tr>' +
      "<th>종목</th><th>수량</th><th>평균단가</th><th>현재가</th><th>평가금액</th><th>평가손익</th><th>수익률</th><th></th>" +
      "</tr></thead><tbody>" + rows + "</tbody></table>";

    var totalPnl = totalCost > 0 ? totalEval - totalCost : null;
    var totalPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : null;
    var pcls = totalPnl == null ? "" : (totalPnl >= 0 ? "pf-up" : "pf-down");
    var psign = totalPnl != null && totalPnl > 0 ? "+" : "";
    summary.innerHTML =
      '<div class="cell"><div class="label">평가금액</div><div class="val">' + pfNumber(totalEval) + " 원</div></div>" +
      '<div class="cell"><div class="label">매입금액</div><div class="val">' + (totalCost > 0 ? pfNumber(totalCost) + " 원" : "—") + "</div></div>" +
      '<div class="cell"><div class="label">평가손익</div><div class="val ' + pcls + '">' + (totalPnl != null ? psign + pfNumber(totalPnl) + " 원" : "—") + "</div></div>" +
      '<div class="cell"><div class="label">수익률</div><div class="val ' + pcls + '">' + (totalPct != null ? psign + totalPct.toFixed(2) + "%" : "—") + "</div></div>";
  }

  // ==== 가격 알림 =================================================================================
  // 목표가를 등록하면 실시간 시세가 조건(이상/이하)에 도달할 때 브라우저 알림·토스트·소리로 알린다.
  // sa.alerts = [{ code, name, op:"ge"|"le", price, on, hit }]  (localStorage)
  var ALERTS = LS.get("alerts", []);
  function saveAlerts() { LS.set("alerts", ALERTS); }
  function alertCodes() { return ALERTS.filter(function (a) { return a.on; }).map(function (a) { return a.code; }); }

  function injectAlertsSection(beforeEl) {
    if (document.getElementById("stock-alerts")) return;
    var st = document.createElement("style");
    st.textContent =
      "#stock-alerts{margin:6px 0 14px}" +
      "#al-form{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0;padding:10px;" +
      "background:#0a0e14;border:1px solid #1c2430;border-radius:6px}" +
      "#al-form input{font-family:inherit;font-size:12px;background:#0d1219;border:1px solid #1c2430;" +
      "color:#e6ebf2;border-radius:4px;padding:6px 8px;width:120px}" +
      "#al-form .al-seg{display:inline-flex;gap:2px;background:#0d1219;border:1px solid #1c2430;border-radius:5px;padding:2px}" +
      "#al-form .al-seg button{font-family:inherit;font-size:11.5px;padding:5px 10px;border:none;border-radius:4px;background:none;color:#8a95a8;cursor:pointer}" +
      "#al-form .al-seg button.on{background:#1c2430;color:#e6ebf2;font-weight:700}" +
      ".al-table{width:100%;border-collapse:collapse;font-size:12px}" +
      ".al-table th,.al-table td{padding:7px 9px;border-bottom:1px solid #141a23;text-align:right;white-space:nowrap}" +
      ".al-table th{color:#5a6577;font-weight:600;font-size:10.5px}" +
      ".al-table th:first-child,.al-table td:first-child{text-align:left}" +
      ".al-name{color:#e6ebf2;font-weight:700}.al-code{color:#5a6577;font-size:10px}" +
      ".al-del{cursor:pointer;color:#5a6577;border:none;background:none;font-size:14px}.al-del:hover{color:#ff5d6c}" +
      ".al-badge{font-size:10.5px;padding:2px 7px;border-radius:3px;border:1px solid #1c2430}" +
      ".al-badge.wait{color:#8a95a8}.al-badge.hit{color:#1fd6a8;border-color:#1c3a32;background:rgba(31,214,168,.08)}" +
      ".al-badge.off{color:#5a6577}" +
      ".al-empty{color:#5a6577;font-size:12px;padding:14px 4px}";
    document.head.appendChild(st);

    var wrap = document.createElement("div");
    wrap.id = "stock-alerts";
    wrap.innerHTML =
      '<div class="section-line" style="flex-wrap:wrap;gap:10px">' +
      '  <div class="section-title" style="margin:0">가격 알림</div>' +
      '  <div class="inline-tools"><button class="mini-btn" id="al-csv" title="CSV로 내보내기">CSV</button><span style="font-size:11px;color:#5a6577">목표가 도달 시 알림·소리</span></div>' +
      "</div>" +
      '<div id="al-form">' +
      '  <input id="al-code" placeholder="종목코드 (예: 005930)" autocomplete="off">' +
      '  <div class="al-seg" id="al-op"><button data-op="ge" class="on">이상 ≥</button><button data-op="le">이하 ≤</button></div>' +
      '  <input id="al-price" type="number" min="0" step="1" placeholder="목표가">' +
      '  <button class="mini-btn on" id="al-add">알림 추가</button>' +
      '  <button class="mini-btn" id="al-perm">데스크톱 알림 켜기</button>' +
      "</div>" +
      '<div id="al-table"></div>';
    if (beforeEl && beforeEl.parentNode) beforeEl.parentNode.insertBefore(wrap, beforeEl);
    else document.body.appendChild(wrap);

    var opSeg = wrap.querySelector("#al-op"), curOp = "ge";
    opSeg.addEventListener("click", function (e) {
      var b = e.target.closest("[data-op]"); if (!b) return;
      curOp = b.getAttribute("data-op");
      opSeg.querySelectorAll("[data-op]").forEach(function (x) { x.classList.toggle("on", x === b); });
    });
    wrap.querySelector("#al-add").addEventListener("click", function () {
      var code = cleanCode(wrap.querySelector("#al-code").value);
      var price = parseFloat(wrap.querySelector("#al-price").value);
      if (!code || !isFinite(price) || price <= 0) { if (window.saToast) window.saToast("종목코드와 목표가를 입력하세요.", "warn"); return; }
      ALERTS.push({ code: code, name: nameOf(code), op: curOp, price: price, on: true, hit: false });
      saveAlerts();
      wrap.querySelector("#al-code").value = ""; wrap.querySelector("#al-price").value = "";
      renderAlerts();
      refreshBoard(true);
    });
    wrap.querySelector("#al-csv").addEventListener("click", function () {
      var rows = [["종목코드", "종목명", "조건", "목표가", "상태"]];
      ALERTS.forEach(function (a) {
        rows.push([a.code, a.name || nameOf(a.code), a.op === "ge" ? "이상" : "이하", a.price, !a.on ? "꺼짐" : (a.hit ? "도달" : "대기")]);
      });
      downloadCSV("alerts_" + new Date().toISOString().slice(0, 10) + ".csv", rows);
    });
    wrap.querySelector("#al-perm").addEventListener("click", function () {
      if (window.Notification && Notification.requestPermission) Notification.requestPermission().then(function (p) {
        if (window.saToast) window.saToast(p === "granted" ? "데스크톱 알림이 켜졌습니다." : "알림 권한이 거부되었습니다.", p === "granted" ? "info" : "warn");
      });
    });
    wrap.querySelector("#al-table").addEventListener("click", function (e) {
      var del = e.target.closest("[data-al-del]");
      if (del) { ALERTS.splice(+del.getAttribute("data-al-del"), 1); saveAlerts(); renderAlerts(); return; }
      var tog = e.target.closest("[data-al-tog]");
      if (tog) { var i = +tog.getAttribute("data-al-tog"); ALERTS[i].on = !ALERTS[i].on; if (ALERTS[i].on) ALERTS[i].hit = false; saveAlerts(); renderAlerts(); refreshBoard(true); }
    });
  }

  function renderAlerts() {
    var table = document.getElementById("al-table");
    if (!table) return;
    if (!ALERTS.length) { table.innerHTML = '<div class="al-empty">등록된 알림이 없습니다. 종목코드와 목표가를 입력해 추가하세요.</div>'; return; }
    table.innerHTML =
      '<table class="al-table"><thead><tr><th>종목</th><th>조건</th><th>목표가</th><th>현재가</th><th>상태</th><th></th></tr></thead><tbody>' +
      ALERTS.map(function (a, i) {
        var e = boardCache[a.code];
        var cur = e ? e.price : null;
        var badge = !a.on ? '<span class="al-badge off">꺼짐</span>' : (a.hit ? '<span class="al-badge hit">도달</span>' : '<span class="al-badge wait">대기</span>');
        return "<tr>" +
          '<td><div class="al-name">' + esc(a.name || nameOf(a.code)) + '</div><div class="al-code">' + esc(a.code) + "</div></td>" +
          "<td>" + (a.op === "ge" ? "이상 ≥" : "이하 ≤") + "</td>" +
          "<td>" + pfNumber(a.price, 2) + "</td>" +
          "<td>" + (cur != null ? pfNumber(cur, 2) : "…") + "</td>" +
          '<td><button class="al-badge ' + (a.on ? "wait" : "off") + '" data-al-tog="' + i + '" style="cursor:pointer">' + (a.on ? "켜짐" : "꺼짐") + "</button> " + badge + "</td>" +
          '<td><button class="al-del" data-al-del="' + i + '" title="삭제">×</button></td>' +
          "</tr>";
      }).join("") + "</tbody></table>";
  }

  function checkAlerts() {
    var fired = false;
    ALERTS.forEach(function (a) {
      if (!a.on || a.hit) return;
      var e = boardCache[a.code];
      if (!e || e.price == null) return;
      var hit = a.op === "ge" ? e.price >= a.price : e.price <= a.price;
      if (hit) {
        a.hit = true; fired = true;
        var nm = a.name || nameOf(a.code);
        if (window.saNotify) window.saNotify("🔔 가격 알림 · " + nm,
          nm + " " + (a.op === "ge" ? "≥ " : "≤ ") + pfNumber(a.price) + "원 도달 (현재 " + pfNumber(e.price) + "원)", "alert");
      }
    });
    if (fired) { saveAlerts(); renderAlerts(); }
  }

  // ==== 섹션 바로가기 네비게이션 ====================================================================
  // 페이지가 길어 원하는 섹션을 찾기 어려우므로, 헤더에 붙는 버튼 바로 각 섹션으로 점프한다.
  // 스크롤 위치에 따라 현재 섹션 버튼을 강조(scroll-spy)한다.
  function injectSectionNav() {
    var hd = document.querySelector(".hd");
    if (!hd || document.getElementById("stock-secnav")) return;
    var SECTIONS = [
      { id: "sec-quote", label: "종목조회" },
      { id: "sec-analysis", label: "기업분석" },
      { id: "stock-portfolio", label: "내 포트폴리오" },
      { id: "stock-alerts", label: "가격 알림" },
      { id: "global-board", label: "글로벌마켓" },
      { id: "sec-ai", label: "AI매매" }
    ].filter(function (s) { return document.getElementById(s.id); });
    if (SECTIONS.length < 2) return;

    var st = document.createElement("style");
    st.textContent =
      "#stock-secnav{display:flex;gap:4px;overflow-x:auto;padding:7px 22px;background:#0b1017;" +
      "border-top:1px solid #141a23;-webkit-overflow-scrolling:touch;scrollbar-width:none}" +
      "#stock-secnav::-webkit-scrollbar{display:none}" +
      "#stock-secnav .secnav-btn{flex:0 0 auto;font-family:'JetBrains Mono',monospace;font-size:11.5px;" +
      "padding:5px 12px;border-radius:5px;border:1px solid #1c2430;background:#0a0e14;color:#8a95a8;" +
      "cursor:pointer;white-space:nowrap}" +
      "#stock-secnav .secnav-btn:hover{color:#e6ebf2;border-color:#3a4658}" +
      "#stock-secnav .secnav-btn.on{background:#1c2430;color:#e6ebf2;font-weight:700;border-color:#3a4658}";
    document.head.appendChild(st);

    var nav = document.createElement("div");
    nav.id = "stock-secnav";
    nav.innerHTML = SECTIONS.map(function (s) {
      return '<button class="secnav-btn" data-sec="' + s.id + '">' + esc(s.label) + "</button>";
    }).join("");
    hd.appendChild(nav);

    function headerOffset() { return hd.offsetHeight + 6; }
    nav.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-sec]");
      if (!btn) return;
      var el = document.getElementById(btn.getAttribute("data-sec"));
      if (!el) return;
      var y = el.getBoundingClientRect().top + window.scrollY - headerOffset();
      window.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
    });

    function updateActive() {
      var off = headerOffset() + 16;
      var current = SECTIONS[0].id;
      SECTIONS.forEach(function (s) {
        var el = document.getElementById(s.id);
        if (el && el.getBoundingClientRect().top <= off) current = s.id;
      });
      // 페이지 맨 아래면 마지막 섹션을 강조(마지막 섹션이 화면 상단까지 못 오는 경우 대비)
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        current = SECTIONS[SECTIONS.length - 1].id;
      }
      nav.querySelectorAll(".secnav-btn").forEach(function (b) {
        b.classList.toggle("on", b.getAttribute("data-sec") === current);
      });
    }
    var raf = null;
    window.addEventListener("scroll", function () {
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = null; updateActive(); });
    }, { passive: true });
    window.addEventListener("resize", updateActive);
    window.__syncSecNav = updateActive;   // 보드/포트폴리오 재렌더로 레이아웃이 바뀌면 다시 계산
    updateActive();
  }

  function injectBoardSection() {
    var statGrid = document.querySelector(".market-stat-grid");
    if (!statGrid || document.getElementById("global-board")) return;
    var wrap = document.createElement("div");
    wrap.id = "global-board";
    wrap.innerHTML =
      '<div class="section-line" style="margin-top:2px;flex-wrap:wrap;gap:10px">' +
      '  <div class="coin-section-title">' +
      '    <div class="section-title" style="margin:0">글로벌 마켓 라이브</div>' +
      '    <div class="coin-section-nav" id="sa-tabs"></div>' +
      "  </div>" +
      '  <div class="inline-tools sa-search-wrap">' +
      '    <input class="news-search" id="sa-search" placeholder="전 세계 종목 검색 (삼성, apple, 0700...)" autocomplete="off">' +
      '    <span class="coin-market-count" id="global-board-upd">—</span>' +
      '    <div class="sa-search-results" id="sa-search-results" hidden></div>' +
      "  </div>" +
      "</div>" +
      '<div class="coin-market-board-grid" id="sa-board-grid" style="grid-template-columns:repeat(6,minmax(0,1fr))"></div>';
    statGrid.parentNode.insertBefore(wrap, statGrid.nextSibling);

    // 탭
    var tabsEl = wrap.querySelector("#sa-tabs");
    tabsEl.innerHTML = MARKET_TABS.map(function (t) {
      return '<button class="coin-section-btn' + (t.id === activeTab ? " on" : "") +
        '" data-market-tab="' + t.id + '">' + esc(t.label) + "</button>";
    }).join("");
    tabsEl.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-market-tab]");
      if (!btn) return;
      activeTab = btn.getAttribute("data-market-tab");
      LS.set("tab", activeTab);
      tabsEl.querySelectorAll(".coin-section-btn").forEach(function (b) {
        b.classList.toggle("on", b === btn);
      });
      renderBoard();
      refreshBoard(true);
    });

    // 카드 클릭(차트 로드) + 별(즐겨찾기)
    wrap.addEventListener("click", function (e) {
      var star = e.target.closest("[data-star]");
      if (star) {
        e.stopPropagation();
        toggleFav(star.getAttribute("data-star"));
        renderBoard();
        return;
      }
      var card = e.target.closest("[data-board-code]");
      if (!card) return;
      var input = document.getElementById("stock-code");
      if (input) input.value = card.getAttribute("data-board-code");
      if (typeof window.loadStock === "function") window.loadStock();
      var chart = document.getElementById("stockSvg");
      if (chart) chart.scrollIntoView({ behavior: "smooth", block: "center" });
    });

    setupSearch(wrap);
  }

  function cardHtml(code) {
    var name = nameOf(code), e = boardCache[code];
    var star = '<span class="sa-star' + (isFav(code) ? " on" : "") + '" data-star="' +
      esc(code) + '" title="즐겨찾기">' + (isFav(code) ? "★" : "☆") + "</span>";
    if (!e) {
      return '<button class="coin-mini-card" data-board-code="' + esc(code) + '">' +
        '<div class="coin-mini-head"><div><div class="coin-mini-symbol">' + esc(name) + "</div>" +
        '<div class="coin-mini-name">' + esc(code) + "</div></div>" +
        '<div style="margin-left:auto">' + star + "</div></div>" +
        '<div class="coin-mini-price muted">로딩…</div></button>';
    }
    var up = (e.change_pct || 0) >= 0;
    var col = up ? "#1fd6a8" : "#ff5d6c";
    var path = sparkPath(e.closes, 200, 58);
    return '<button class="coin-mini-card" data-board-code="' + esc(code) + '">' +
      '<div class="coin-mini-head"><div>' +
      '<div class="coin-mini-symbol">' + esc(name) + "</div>" +
      '<div class="coin-mini-name">' + esc(code) + "</div></div>" +
      '<div style="margin-left:auto;display:flex;align-items:center;gap:4px">' +
      '<div class="coin-mini-change ' + (up ? "up" : "down") + '">' + (up ? "+" : "") +
      Number(e.change_pct || 0).toFixed(2) + "%</div>" + star + "</div></div>" +
      '<div class="coin-mini-price">' + esc(priceLabel(code, e.price, e.currency)) + "</div>" +
      '<svg class="coin-mini-chart" viewBox="0 0 200 58" preserveAspectRatio="none">' +
      (path ? '<path d="' + path + '" fill="none" stroke="' + col + '" stroke-width="1.6" vector-effect="non-scaling-stroke"></path>' : "") +
      "</svg></button>";
  }

  function renderBoard() {
    var grid = document.getElementById("sa-board-grid");
    if (!grid) return;
    var updEl = document.getElementById("global-board-upd");
    if (updEl) {
      updEl.textContent = boardUpdatedAt
        ? "갱신 " + boardUpdatedAt.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
        : "이전 시세 표시 중 · 갱신 대기";
    }
    var tab = MARKET_TABS.filter(function (t) { return t.id === activeTab; })[0] || MARKET_TABS[0];
    var codes = tab.codes();
    if (!codes.length) {
      grid.innerHTML = '<div class="sa-empty-note">즐겨찾기가 비어 있습니다. 카드나 검색 결과의 ☆를 눌러 추가하세요.</div>';
      return;
    }
    grid.innerHTML = codes.map(cardHtml).join("");
  }

  // ---- 전종목 검색 (Yahoo search API) ------------------------------------------------------------
  var SEARCH_TYPES = { EQUITY: "주식", ETF: "ETF", INDEX: "지수", CURRENCY: "환율", CRYPTOCURRENCY: "코인" };

  // 큐레이션 사전에서 한글명/코드 부분일치 (야후는 한글 검색이 약해서 로컬 우선)
  function localMatches(q) {
    var needle = q.toLowerCase();
    var out = [];
    var seen = {};
    var scan = function (code, name) {
      if (seen[code]) return;
      if (String(name).toLowerCase().indexOf(needle) !== -1 ||
          String(code).toLowerCase().indexOf(needle) !== -1) {
        seen[code] = true;
        out.push({ symbol: code, name: name, exch: "", type: "저장됨" });
      }
    };
    Object.keys(NAME_OF).forEach(function (c) { scan(c, NAME_OF[c]); });
    Object.keys(EXTRA_NAMES).forEach(function (c) { scan(c, EXTRA_NAMES[c]); });
    return out.slice(0, 6);
  }

  function remoteSearch(q) {
    var path = "/v1/finance/search?q=" + encodeURIComponent(q) +
      "&quotesCount=12&newsCount=0&listsCount=0&lang=ko-KR&region=KR";
    return fetchYahoo(path).then(function (json) {
      return (json.quotes || []).filter(function (t) {
        return t.symbol && SEARCH_TYPES[t.quoteType];
      }).map(function (t) {
        return {
          symbol: codeOfSymbol(t.symbol),
          name: t.shortname || t.longname || t.symbol,
          exch: t.exchDisp || t.exchange || "",
          type: SEARCH_TYPES[t.quoteType]
        };
      });
    });
  }

  function dedupeResults(items) {
    var seen = {};
    return items.filter(function (it) {
      if (seen[it.symbol]) return false;
      seen[it.symbol] = true;
      return true;
    });
  }

  // 야후 검색은 한글 종목명을 못 찾는다("삼성전자" → 0건). 국내 종목은
  // 네이버 자동완성으로 한글 이름·초성 검색을 지원한다(번호 없이 이름만으로 검색).
  function koreanSearch(q) {
    if (!/[가-힣]/.test(q)) return Promise.resolve([]);  // 한글이 들어간 질의만 네이버로
    var url = "https://m.stock.naver.com/front-api/search/autoComplete?query=" +
      encodeURIComponent(q) + "&target=stock,index,marketindicator";
    return fetchProxied(url, function (j) {
      return j && j.result && Array.isArray(j.result.items);
    }).then(function (json) {
      return json.result.items.filter(function (it) {
        return it.code && /^\d{6}$/.test(it.code) && it.nationCode === "KOR" &&
          (it.category === "stock" || it.category === "index");
      }).map(function (it) {
        // 코스닥은 .KQ, 코스피는 .KS로 미리 해석 힌트를 심어 차트 로딩을 빠르게 한다.
        if (!RESOLVED[it.code]) {
          RESOLVED[it.code] = it.code + (it.typeCode === "KOSDAQ" ? ".KQ" : ".KS");
          LS.set("resolved", RESOLVED);
        }
        return {
          symbol: it.code,
          name: it.name,
          exch: it.typeName || "KRX",
          type: it.category === "index" ? "지수" : "주식"
        };
      }).slice(0, 8);
    }).catch(function () { return []; });
  }

  function setupSearch(wrap) {
    var input = wrap.querySelector("#sa-search");
    var panel = wrap.querySelector("#sa-search-results");
    var timer = null, lastQuery = "";

    function close() { panel.hidden = true; }

    function renderResults(items, q) {
      if (!items.length) {
        panel.innerHTML = '<div class="sa-empty-note">"' + esc(q) + '" 검색 결과 없음</div>';
        panel.hidden = false;
        return;
      }
      panel.innerHTML = items.map(function (it) {
        return '<div class="sa-search-row" data-search-code="' + esc(it.symbol) + '" data-search-name="' + esc(it.name) + '">' +
          "<div><div class=\"nm\">" + esc(it.name) + '</div><div class="sub">' + esc(it.symbol) + "</div></div>" +
          '<span class="ex">' + esc(it.exch) + " · " + esc(it.type) + "</span>" +
          '<span class="sa-star' + (isFav(it.symbol) ? " on" : "") + '" data-search-star="' + esc(it.symbol) + '">' +
          (isFav(it.symbol) ? "★" : "☆") + "</span></div>";
      }).join("");
      panel.hidden = false;
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      clearTimeout(timer);
      if (q.length < 1) { close(); return; }
      timer = setTimeout(function () {
        lastQuery = q;
        // 1) 로컬 사전 매칭은 즉시 표시 (야후 응답을 기다리지 않는다)
        var local = localMatches(q);
        if (local.length) renderResults(local, q);
        else {
          panel.innerHTML = '<div class="sa-empty-note">검색 중…</div>';
          panel.hidden = false;
        }
        // 2) 원격 검색: 네이버(국내 한글명) + 야후(글로벌)를 병렬로 병합해 다시 그린다.
        //    한글 종목명은 네이버에서만 나오므로 국내 결과를 앞에 배치한다.
        Promise.all([
          koreanSearch(q).catch(function () { return []; }),
          remoteSearch(q).catch(function () { return []; })
        ]).then(function (res) {
          if (q !== lastQuery) return;
          var items = dedupeResults(res[0].concat(local).concat(res[1])).slice(0, 10);
          if (!items.length) { renderResults([], q); return; }
          items.forEach(function (it) { rememberName(it.symbol, it.name); });
          renderResults(items, q);
        }).catch(function () {
          if (q === lastQuery && !local.length) {
            panel.innerHTML = '<div class="sa-empty-note">검색 실패 — 잠시 후 다시 시도하세요.</div>';
          }
        });
      }, 350);
    });

    panel.addEventListener("click", function (e) {
      var star = e.target.closest("[data-search-star]");
      if (star) {
        e.stopPropagation();
        var code = star.getAttribute("data-search-star");
        var on = toggleFav(code);
        star.classList.toggle("on", on);
        star.textContent = on ? "★" : "☆";
        renderBoard();
        refreshBoard(true);
        return;
      }
      var row = e.target.closest("[data-search-code]");
      if (!row) return;
      var sym = row.getAttribute("data-search-code");
      rememberName(sym, row.getAttribute("data-search-name"));
      var codeEl = document.getElementById("stock-code");
      if (codeEl) codeEl.value = sym;
      if (typeof window.loadStock === "function") window.loadStock();
      close();
      var chart = document.getElementById("stockSvg");
      if (chart) chart.scrollIntoView({ behavior: "smooth", block: "center" });
    });

    document.addEventListener("click", function (e) {
      if (!wrap.querySelector(".sa-search-wrap").contains(e.target)) close();
    });
    input.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  }

  // ---- 하단 안내 ----------------------------------------------------------------------------------
  var foot = document.getElementById("foot");
  if (foot) {
    foot.textContent = "실시간 시세: Yahoo Finance(한국·미국·유럽·홍콩·일본·지수·환율) · " +
      "CORS 프록시 경유 · 거래소별 최대 15~20분 지연 가능 · ★ 즐겨찾기는 이 브라우저에 저장";
  }

  // ---- 시작 --------------------------------------------------------------------------------------
  // 목업이 이미 그린 가짜 시세가 헷갈리지 않도록 즉시 로딩 표시로 바꾼다.
  ["stock-price", "stock-change", "stock-rsi"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.textContent = "로딩…";
  });
  var stEl0 = document.getElementById("stock-status");
  if (stEl0) stEl0.textContent = "로딩…";

  injectBoardSection();
  injectPortfolioSection(document.getElementById("global-board"));  // 내 포트폴리오를 글로벌 보드 위에
  injectAlertsSection(document.getElementById("global-board"));      // 가격 알림 섹션(포트폴리오 아래)
  injectSectionNav();               // 섹션 바로가기 버튼(헤더 하단)
  renderBoard();                    // localStorage 캐시가 있으면 즉시 그려짐
  renderPortfolio();                // 캐시된 시세로 즉시 표시
  renderAlerts();                   // 캐시된 시세로 알림 상태 즉시 표시
  if (Object.keys(boardCache).length && typeof window.loadTickerTape === "function") {
    liveTape().then(function () { window.loadTickerTape(); }).catch(function () {});
  }
  refreshBoard(true);
  // 자동 새로고침 주기: 설정(sa.pref.refresh)에 따라 인터벌을 조절한다("off"면 수동만).
  var boardTimer = null;
  window.saApplyRefresh = function (v) {
    if (boardTimer) { clearInterval(boardTimer); boardTimer = null; }
    var sec = v === "10" ? 10 : v === "30" ? 30 : v === "off" ? 0 : 60;
    if (sec > 0) boardTimer = setInterval(function () { refreshBoard(false); }, sec * 1000);
  };
  window.saApplyRefresh(localStorage.getItem("sa.pref.refresh") || "60");
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshBoard(false);
  });
  if (typeof window.loadStock === "function") window.loadStock();
})();
