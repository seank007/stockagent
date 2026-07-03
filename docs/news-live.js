/*
 * news-live.js — GitHub Pages(정적)에서도 실시간 뉴스를 띄우기 위한 브라우저 뉴스 수집기.
 *
 * 서버가 없으므로 여러 개의 공개 CORS 프록시를 순차 폴백하며 실제 RSS 피드를
 * 브라우저에서 직접 가져와 파싱한다. 코인/주식 두 부류의 다수 소스를 병합·중복
 * 제거·시간정렬한 뒤, 기존 UI가 호출하는 /api/coin/news · /api/stocks/news ·
 * /api/coin/news_summary 응답을 실제 데이터로 대체한다.
 *
 * 이 스크립트는 정적 목업(window.fetch 오버라이드) "다음"에 로드되어 fetch를 한 번
 * 더 감싼다. 라이브 수집이 모두 실패하면 원래(목업) 응답으로 우아하게 폴백한다.
 */
(function () {
  "use strict";

  var nativeFetch = window.fetch.bind(window);

  // ---- 공개 CORS 프록시 (순차 폴백) -----------------------------------------
  // 하나가 죽어도 다음으로 넘어간다. 브라우저 Origin에서만 통과하는 프록시 포함.
  var PROXIES = [
    { build: function (u) { return "https://api.codetabs.com/v1/proxy/?quest=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://corsproxy.io/?url=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.allorigins.win/raw?url=" + encodeURIComponent(u); } },
    { build: function (u) { return "https://api.allorigins.win/get?url=" + encodeURIComponent(u); }, json: true },
    { build: function (u) { return "https://thingproxy.freeboard.io/fetch/" + u; } }
  ];
  var FEED_TIMEOUT_MS = 5500;   // 프록시 1회 시도 타임아웃
  var FEED_DEADLINE_MS = 9000;  // 피드 1개당 총 상한(죽은 소스가 전체를 지연시키지 않도록)
  var CACHE_MS = 30000;

  function gnews(query, korean) {
    var tail = korean ? "hl=ko&gl=KR&ceid=KR:ko" : "hl=en-US&gl=US&ceid=US:en";
    return "https://news.google.com/rss/search?q=" + encodeURIComponent(query) + "&" + tail;
  }

  // ---- 코인 뉴스 소스 --------------------------------------------------------
  var COIN_FEEDS = [
    { name: "Google 코인", url: gnews("비트코인 OR 암호화폐 OR 가상자산 OR 이더리움", true), via: "rss2json", filter: true, limit: 30, live: true },
    { name: "Google Crypto", url: gnews("bitcoin OR ethereum OR crypto OR cryptocurrency", false), via: "rss2json", filter: true, limit: 30, live: true },
    { name: "Google 업비트", url: gnews("업비트 OR 빗썸 OR 코인 상장 OR 김치프리미엄", true), via: "rss2json", filter: true, limit: 20, live: true },
    { name: "CoinDesk", url: "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml", limit: 24 },
    { name: "Cointelegraph", url: "https://cointelegraph.com/rss", limit: 24 },
    { name: "Decrypt", url: "https://decrypt.co/feed", limit: 20 },
    { name: "NewsBTC", url: "https://www.newsbtc.com/feed/", limit: 16 },
    { name: "CryptoSlate", url: "https://cryptoslate.com/feed/", limit: 16 },
    { name: "Bitcoinist", url: "https://bitcoinist.com/feed/", limit: 16 },
    { name: "CryptoPotato", url: "https://cryptopotato.com/feed/", limit: 16 },
    { name: "AMBCrypto", url: "https://ambcrypto.com/feed/", limit: 16 },
    { name: "U.Today", url: "https://u.today/rss", limit: 16 },
    { name: "BeInCrypto", url: "https://beincrypto.com/feed/", limit: 16 },
    { name: "CoinGape", url: "https://coingape.com/feed/", limit: 16 },
    { name: "Bitcoin Magazine", url: "https://bitcoinmagazine.com/feed", limit: 14 },
    { name: "CryptoBriefing", url: "https://cryptobriefing.com/feed/", limit: 14 },
    { name: "Blockmedia", url: "https://www.blockmedia.co.kr/feed/", filter: true, limit: 20 },
    { name: "TokenPost", url: "https://www.tokenpost.kr/rss", filter: true, limit: 20 }
  ];

  // ---- 주식 뉴스 소스 --------------------------------------------------------
  var STOCK_FEEDS = [
    { name: "Google 증시", url: gnews("코스피 OR 코스닥 OR 증시 OR 주식시장", true), via: "rss2json", limit: 30, live: true },
    { name: "Google 종목", url: gnews("삼성전자 OR SK하이닉스 OR 반도체 OR 2차전지", true), via: "rss2json", limit: 24, live: true },
    { name: "Google Markets", url: gnews("stock market OR S&P 500 OR nasdaq OR dow jones", false), via: "rss2json", limit: 30, live: true },
    { name: "Yahoo Finance", url: "https://finance.yahoo.com/news/rssindex", limit: 24 },
    { name: "CNBC", url: "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", limit: 20 },
    { name: "MarketWatch", url: "https://feeds.marketwatch.com/marketwatch/topstories/", limit: 20 },
    { name: "Investing.com", url: "https://www.investing.com/rss/news_25.rss", limit: 16 },
    { name: "한국경제", url: "https://www.hankyung.com/feed/finance", limit: 20 },
    { name: "이데일리 증권", url: "https://rss.edaily.co.kr/stock_news.xml", limit: 16 }
  ];

  var CRYPTO_KEYWORDS = [
    "비트코인", "이더리움", "리플", "솔라나", "도지", "테더", "스테이블코인", "가상자산", "암호화폐",
    "코인", "블록체인", "업비트", "빗썸", "코빗", "토큰", "디파이", "채굴", "김치프리미엄", "알트코인",
    "btc", "bitcoin", "eth", "ethereum", "xrp", "solana", "doge", "usdt", "stablecoin", "altcoin",
    "crypto", "cryptocurrency", "blockchain", "upbit", "bithumb", "token", "defi", "web3", "nft"
  ];

  function looksCrypto(text) {
    var t = (text || "").toLowerCase();
    for (var i = 0; i < CRYPTO_KEYWORDS.length; i++) {
      if (t.indexOf(CRYPTO_KEYWORDS[i]) !== -1) return true;
    }
    return false;
  }

  // ---- HTTP / 파싱 유틸 ------------------------------------------------------
  function fetchWithTimeout(url, json) {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, FEED_TIMEOUT_MS);
    return nativeFetch(url, { signal: ctrl.signal, redirect: "follow" })
      .then(function (res) {
        clearTimeout(timer);
        if (!res.ok) throw new Error("HTTP " + res.status);
        return json ? res.json().then(function (j) { return j && j.contents ? j.contents : ""; }) : res.text();
      })
      .catch(function (e) { clearTimeout(timer); throw e; });
  }

  function looksLikeFeed(text) {
    return !!text && (text.indexOf("<item") !== -1 || text.indexOf("<entry") !== -1 ||
      text.indexOf("<rss") !== -1 || text.indexOf("<feed") !== -1);
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

  // 프록시를 순서대로 시도해 최초로 성공한 피드 XML 텍스트를 반환.
  function fetchFeedText(feedUrl) {
    var idx = 0;
    function attempt() {
      if (idx >= PROXIES.length) return Promise.reject(new Error("모든 프록시 실패"));
      var proxy = PROXIES[idx++];
      return fetchWithTimeout(proxy.build(feedUrl), proxy.json)
        .then(function (text) {
          if (looksLikeFeed(text)) return text;
          throw new Error("피드 형식 아님");
        })
        .catch(function () { return attempt(); });
    }
    return attempt();
  }

  // Google News RSS는 프록시가 데이터센터 IP로 차단당해 잘 안 열린다.
  // rss2json(파싱+CORS 지원)으로 우회한다.
  function fetchViaRss2Json(feed) {
    var api = "https://api.rss2json.com/v1/api.json?rss_url=" + encodeURIComponent(feed.url);
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, FEED_TIMEOUT_MS);
    return nativeFetch(api, { signal: ctrl.signal })
      .then(function (res) { clearTimeout(timer); if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
      .then(function (j) {
        if (!j || j.status !== "ok" || !j.items) throw new Error("rss2json 실패");
        return j.items.map(function (it) {
          return {
            source: feed.name,
            publisher: stripHtml(it.author || (j.feed && j.feed.title) || feed.name, 60) || feed.name,
            title: stripHtml(it.title, 200),
            link: it.link || "#",
            summary: stripHtml(it.description || it.content, 240),
            published: it.pubDate || "",
            published_ts: toTs(it.pubDate),
            live: !!feed.live
          };
        });
      })
      .catch(function (e) { clearTimeout(timer); throw e; });
  }

  var _decoder = document.createElement("textarea");
  function decodeEntities(s) {
    _decoder.innerHTML = s;
    return _decoder.value;
  }
  function stripHtml(s, max) {
    if (!s) return "";
    var out = decodeEntities(String(s).replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();
    if (max && out.length > max) out = out.slice(0, max - 1) + "…";
    return out;
  }

  function directChild(el, names) {
    if (!el) return null;
    var kids = el.children || [];
    for (var i = 0; i < kids.length; i++) {
      var ln = (kids[i].localName || kids[i].nodeName || "").toLowerCase();
      if (names.indexOf(ln) !== -1) return kids[i];
    }
    return null;
  }
  function childText(el, names) {
    var node = directChild(el, names);
    return node ? (node.textContent || "").trim() : "";
  }
  function pickLink(el) {
    var kids = (el && el.children) || [];
    var href = "";
    for (var i = 0; i < kids.length; i++) {
      var ln = (kids[i].localName || kids[i].nodeName || "").toLowerCase();
      if (ln !== "link") continue;
      var text = (kids[i].textContent || "").trim();
      if (text) return text;                       // RSS: <link>URL</link>
      var attr = kids[i].getAttribute && kids[i].getAttribute("href");
      if (attr && (!href || kids[i].getAttribute("rel") === "alternate")) href = attr; // Atom
    }
    return href;
  }

  function toTs(str) {
    if (!str) return 0;
    var t = new Date(str).getTime();
    return isNaN(t) ? 0 : t;
  }

  function parseFeed(text, sourceName, isLive) {
    var doc;
    try { doc = new DOMParser().parseFromString(text, "text/xml"); }
    catch (e) { return []; }
    var nodes = doc.getElementsByTagName("item");
    var atom = false;
    if (!nodes || !nodes.length) { nodes = doc.getElementsByTagName("entry"); atom = true; }
    var rows = [];
    for (var i = 0; i < nodes.length; i++) {
      var it = nodes[i];
      var title = childText(it, ["title"]);
      if (!title) continue;
      var link = pickLink(it);
      var desc = childText(it, ["description", "summary", "encoded", "content"]);
      var dateStr = childText(it, ["pubdate", "date", "published", "updated"]);
      var publisher = childText(it, ["source", "creator", "author"]) || sourceName;
      var ts = toTs(dateStr);
      rows.push({
        source: sourceName,
        publisher: stripHtml(publisher, 60) || sourceName,
        title: stripHtml(title, 200),
        link: link || "#",
        summary: stripHtml(desc, 240),
        published: dateStr || "",
        published_ts: ts,
        live: !!isLive
      });
    }
    return rows;
  }

  function normTitle(title) {
    return (title || "").toLowerCase().replace(/[^0-9a-z가-힣]/g, "").slice(0, 48);
  }

  // 여러 피드를 병렬 수집 → 병합 · 중복제거 · 시간정렬.
  // 죽은 소스가 전체를 지연시키지 않도록 피드 1개당 총 상한(FEED_DEADLINE_MS)을 건다.
  function aggregate(feeds, limit) {
    var tasks = feeds.map(function (feed) {
      var work = feed.via === "rss2json"
        ? fetchViaRss2Json(feed)
        : fetchFeedText(feed.url).then(function (text) { return parseFeed(text, feed.name, feed.live); });
      return withDeadline(work, FEED_DEADLINE_MS).then(function (rows) {
        if (feed.filter) rows = rows.filter(function (r) { return looksCrypto(r.title + " " + r.summary); });
        if (feed.limit) rows = rows.slice(0, feed.limit);
        return { feed: feed, rows: rows, error: rows.length ? null : "항목 없음" };
      }).catch(function (e) {
        return { feed: feed, rows: [], error: (e && e.message) || "응답 없음" };
      });
    });

    return Promise.all(tasks).then(function (settled) {
      var items = [], sources = [], errors = [], seen = {};
      settled.forEach(function (r) {
        var added = 0;
        r.rows.forEach(function (row) {
          var key = normTitle(row.title);
          if (!key || seen[key]) return;
          seen[key] = 1; items.push(row); added++;
        });
        sources.push({ name: r.feed.name, count: added, ok: !r.error && added > 0 });
        if (r.error) errors.push({ source: r.feed.name, error: r.error });
      });
      items.sort(function (a, b) { return (b.published_ts || 0) - (a.published_ts || 0); });
      var stamp = new Date().toISOString();
      return {
        items: items.slice(0, limit || 90),
        total_count: items.length,
        sources: sources,
        errors: errors,
        generated_at: stamp,
        fetched_at: stamp,
        latest_source: items[0] ? items[0].source : null
      };
    });
  }

  // ---- 캐시 래퍼 -------------------------------------------------------------
  var _cache = { coin: null, coinAt: 0, stock: null, stockAt: 0 };
  function cached(kind, feeds, limit, force) {
    var now = Date.now();
    var atKey = kind + "At";
    if (!force && _cache[kind] && (now - _cache[atKey]) < CACHE_MS) {
      return Promise.resolve(_cache[kind]);
    }
    if (!force && _cache[kind + "Pending"]) return _cache[kind + "Pending"];
    var p = aggregate(feeds, limit).then(function (payload) {
      if (payload.items.length) { _cache[kind] = payload; _cache[atKey] = Date.now(); }
      _cache[kind + "Pending"] = null;
      return payload.items.length ? payload : (_cache[kind] || payload);
    }).catch(function () {
      _cache[kind + "Pending"] = null;
      return _cache[kind] || { items: [], total_count: 0, sources: [], errors: [{ source: "network", error: "수집 실패" }] };
    });
    _cache[kind + "Pending"] = p;
    return p;
  }

  // ---- 코인 뉴스 AI 요약(정적: 라이브 데이터에서 간이 생성) -----------------
  var ASSET_DEFS = [["BTC", ["btc", "bitcoin", "비트코인"]], ["ETH", ["eth", "ethereum", "이더리움"]],
    ["XRP", ["xrp", "ripple", "리플"]], ["SOL", ["sol", "solana", "솔라나"]],
    ["DOGE", ["doge", "dogecoin", "도지"]], ["ADA", ["ada", "cardano", "에이다"]]];
  var BULL = ["surge", "rally", "soar", "gain", "jump", "상승", "급등", "강세", "돌파", "신고가"];
  var BEAR = ["plunge", "drop", "fall", "crash", "slump", "하락", "급락", "약세", "청산", "폭락"];

  function deriveSummary(payload) {
    var items = payload.items || [];
    if (!items.length) return null;
    var blob = items.slice(0, 30).map(function (x) { return (x.title + " " + x.summary).toLowerCase(); }).join(" ");
    var bull = 0, bear = 0, w;
    for (w = 0; w < BULL.length; w++) if (blob.indexOf(BULL[w]) !== -1) bull++;
    for (w = 0; w < BEAR.length; w++) if (blob.indexOf(BEAR[w]) !== -1) bear++;
    var mood = bull > bear + 1 ? "강세" : bear > bull + 1 ? "약세" : "혼조";
    var assets = ASSET_DEFS.map(function (d) {
      var c = 0; for (var i = 0; i < d[1].length; i++) { var re = blob.split(d[1][i]).length - 1; c += re; }
      return { sym: d[0], c: c };
    }).filter(function (a) { return a.c > 0; }).sort(function (a, b) { return b.c - a.c; })
      .map(function (a) { return a.sym; });
    var okCount = (payload.sources || []).filter(function (s) { return s.ok; }).length;
    return {
      headline: items[0].title,
      market_mood: mood,
      brief: items.slice(0, 4).map(function (x) { return x.title; }),
      key_assets: assets.length ? assets.slice(0, 5) : ["BTC", "ETH"],
      risks: ["암호화폐 변동성 확대", "규제·거래소 이슈", "레버리지 청산 리스크"],
      watch: ["BTC 주요 지지/저항", "ETF·기관 수급 흐름", "온체인 활동 지표"],
      source_note: "실시간 " + okCount + "개 소스 · 최신 " + items.length + "건 기준",
      generated_at: new Date().toISOString()
    };
  }

  function jsonResponse(obj) {
    return new Response(JSON.stringify(obj), {
      status: 200, headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  // ---- fetch 오버라이드 ------------------------------------------------------
  window.fetch = function (input, init) {
    var raw = typeof input === "string" ? input : (input && input.url) || "";
    var path = "", params = null;
    try {
      var u = new URL(raw, window.location.origin);
      path = u.pathname.replace(/^\/stockagent/, "");
      params = u.searchParams;
    } catch (e) { path = ""; }
    var force = params && (params.get("refresh") || params.get("force"));

    if (path === "/api/coin/news") {
      var climit = params ? parseInt(params.get("limit"), 10) || 90 : 90;
      return cached("coin", COIN_FEEDS, Math.max(climit, 90), force).then(function (payload) {
        if (!payload.items.length) return nativeFetch(input, init); // 목업 폴백
        return jsonResponse(payload);
      }).catch(function () { return nativeFetch(input, init); });
    }
    if (path === "/api/coin/news_summary") {
      return cached("coin", COIN_FEEDS, 90, force).then(function (payload) {
        var summary = deriveSummary(payload);
        if (!summary) return nativeFetch(input, init);
        return jsonResponse({
          summary: summary,
          news_count: payload.items.length,
          sources: payload.sources,
          generated_at: summary.generated_at,
          cached: false,
          latest_news_ts: payload.items[0] ? payload.items[0].published_ts : null
        });
      }).catch(function () { return nativeFetch(input, init); });
    }
    if (path === "/api/stocks/news") {
      var slimit = params ? parseInt(params.get("limit"), 10) || 90 : 90;
      return cached("stock", STOCK_FEEDS, Math.max(slimit, 90), force).then(function (payload) {
        return jsonResponse(payload);
      }).catch(function () {
        return jsonResponse({ items: [], total_count: 0, sources: [], errors: [{ source: "network", error: "수집 실패" }] });
      });
    }
    return nativeFetch(input, init);
  };

  // ---------------------------------------------------------------------------
  // 주식 페이지에는 뉴스 UI가 없으므로 여기서 직접 섹션을 만들어 붙인다.
  // (재-export로 마크업이 사라져도 런타임에 다시 주입되도록 자립형으로 구성)
  // ---------------------------------------------------------------------------
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function ago(ts) {
    if (!ts) return "—";
    var diff = Date.now() - ts;
    if (diff < 60000) return "방금";
    var m = Math.floor(diff / 60000);
    if (m < 60) return m + "분 전";
    var h = Math.floor(m / 60);
    if (h < 24) return h + "시간 전";
    return Math.floor(h / 24) + "일 전";
  }
  function clock(ts) {
    if (!ts) return "";
    var d = new Date(ts);
    return isNaN(d.getTime()) ? "" : d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  var _stock = { items: [], sources: [], errors: [] };

  function renderStockNews() {
    var rows = document.getElementById("stock-news-rows");
    if (!rows) return;
    var qEl = document.getElementById("stock-news-query");
    var q = (qEl && qEl.value || "").trim().toLowerCase();
    var items = _stock.items;
    if (q) items = items.filter(function (it) {
      return (it.title + " " + it.summary + " " + it.publisher + " " + it.source).toLowerCase().indexOf(q) !== -1;
    });
    var total = document.getElementById("stock-news-total");
    if (total) total.textContent = items.length + "건";
    rows.innerHTML = items.map(function (it) {
      return '<div class="news-item">' +
        '<div><span class="news-source-chip">' + esc(it.source || "NEWS") + '</span>' +
        (it.live ? '<span class="news-source-chip live">LIVE</span>' : '') + '</div>' +
        '<div><div class="news-publisher">' + esc(it.publisher || it.source || "") + '</div>' +
        '<a class="news-title" href="' + esc(it.link || "#") + '" target="_blank" rel="noopener noreferrer">' + esc(it.title || "제목 없음") + '</a>' +
        '<div class="news-summary">' + esc(it.summary || "요약 없음") + '</div></div>' +
        '<div class="news-time"><div>' + esc(ago(it.published_ts)) + '</div>' +
        '<div class="muted" style="margin-top:4px">' + esc(clock(it.published_ts)) + '</div></div>' +
        '</div>';
    }).join("") || '<div class="tbl-row muted">표시할 뉴스가 없습니다</div>';
  }

  function renderStockSources() {
    var el = document.getElementById("stock-news-source-rows");
    if (el) {
      el.innerHTML = _stock.sources.map(function (s) {
        return '<div class="news-source-row">' +
          '<span style="font-weight:700;color:#cdd5e0">' + esc(s.name) + '</span>' +
          '<span style="text-align:right;color:#8a95a8">' + (s.count || 0) + '건</span>' +
          '<span class="' + (s.ok ? "news-source-ok" : "news-source-err") + '">' + (s.ok ? "OK" : "ERR") + '</span>' +
          '</div>';
      }).join("") || '<div class="tbl-row muted">소스 없음</div>';
    }
    var okc = _stock.sources.filter(function (s) { return s.ok; }).length;
    var setTxt = function (id, v) { var n = document.getElementById(id); if (n) n.textContent = v; };
    setTxt("stock-news-count", _stock.items.length + "건");
    setTxt("stock-news-sources", okc + "/" + _stock.sources.length);
    setTxt("stock-news-latest", _stock.items[0] ? ago(_stock.items[0].published_ts) : "—");
    setTxt("stock-news-updated", new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }));
    var note = document.getElementById("stock-news-note");
    if (note) {
      note.textContent = _stock.errors.length
        ? (okc + "개 소스 연결 · 일부 오류 " + _stock.errors.length + "건")
        : (okc + "개 소스 연결 · 실제 최신 뉴스 피드");
      note.style.color = _stock.errors.length ? "#e0b341" : "#5a6577";
    }
  }

  var _stockLoading = false;
  function loadStockNews(force) {
    if (_stockLoading) return;
    _stockLoading = true;
    var note = document.getElementById("stock-news-note");
    if (note && !_stock.items.length) { note.textContent = "뉴스를 불러오는 중입니다."; note.style.color = "#5a6577"; }
    cached("stock", STOCK_FEEDS, 90, force).then(function (payload) {
      _stock.items = payload.items || [];
      _stock.sources = payload.sources || [];
      _stock.errors = payload.errors || [];
      renderStockNews();
      renderStockSources();
    }).catch(function () {}).then(function () { _stockLoading = false; });
  }

  function buildStockNewsSection() {
    if (!/\/stocks\/?($|[?#])/.test(window.location.pathname)) return;
    if (document.getElementById("stock-news-rows")) return;
    var wrap = document.querySelector(".wrap");
    if (!wrap) return;
    var box = document.createElement("div");
    box.innerHTML =
      '<div class="section-line" style="margin-top:26px">' +
      '  <div class="section-title">주식 뉴스</div>' +
      '  <div class="market-actions">' +
      '    <input id="stock-news-query" class="news-search" placeholder="뉴스 검색" autocomplete="off">' +
      '    <button class="mini-btn" id="stock-news-refresh">새로고침</button>' +
      '  </div>' +
      '</div>' +
      '<div class="market-stat-grid">' +
      '  <div class="market-stat"><div class="label"><span class="news-live-dot"></span>실시간 뉴스</div><div class="val" id="stock-news-count">—</div></div>' +
      '  <div class="market-stat"><div class="label">연결 소스</div><div class="val" id="stock-news-sources">—</div></div>' +
      '  <div class="market-stat"><div class="label">최신</div><div class="val" id="stock-news-latest">—</div></div>' +
      '  <div class="market-stat"><div class="label">갱신</div><div class="val" id="stock-news-updated">—</div></div>' +
      '</div>' +
      '<div class="coin-news-grid">' +
      '  <div class="box">' +
      '    <div class="box-head"><span>LATEST STOCK NEWS</span><span class="total" id="stock-news-total">0건</span></div>' +
      '    <div id="stock-news-rows"></div>' +
      '    <div class="news-note" id="stock-news-note">뉴스를 불러오는 중입니다.</div>' +
      '  </div>' +
      '  <div class="box">' +
      '    <div class="box-head"><span>NEWS SOURCES</span></div>' +
      '    <div id="stock-news-source-rows"></div>' +
      '  </div>' +
      '</div>';
    var foot = document.getElementById("foot");
    if (foot && foot.parentNode === wrap) wrap.insertBefore(box, foot);
    else wrap.appendChild(box);

    var input = document.getElementById("stock-news-query");
    if (input) input.addEventListener("input", renderStockNews);
    var btn = document.getElementById("stock-news-refresh");
    if (btn) btn.addEventListener("click", function () { loadStockNews(true); });

    loadStockNews(false);
    setInterval(function () { if (!document.hidden) loadStockNews(false); }, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildStockNewsSection);
  } else {
    buildStockNewsSection();
  }
})();
