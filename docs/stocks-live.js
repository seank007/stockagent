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
  var QUOTE_TTL = 45000;
  var HIST_TTL = 5 * 60000;
  var BOARD_REFRESH_MS = 60000;

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
    return refreshCodes(codes)
      .then(function () {
        boardUpdatedAt = new Date();
        LS.set("board", boardCache);
        renderBoard();
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
        // 2) 야후 전종목 검색 결과가 도착하면 병합해 다시 그린다
        remoteSearch(q).then(function (remote) {
          if (q !== lastQuery) return;
          var items = dedupeResults(local.concat(remote));
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
  renderBoard();                    // localStorage 캐시가 있으면 즉시 그려짐
  if (Object.keys(boardCache).length && typeof window.loadTickerTape === "function") {
    liveTape().then(function () { window.loadTickerTape(); }).catch(function () {});
  }
  refreshBoard(true);
  setInterval(function () { refreshBoard(false); }, BOARD_REFRESH_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshBoard(false);
  });
  if (typeof window.loadStock === "function") window.loadStock();
})();
