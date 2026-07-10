/*
 * ai-live.js — GitHub Pages에서 "AI 거래" 탭의 계좌를 실시간으로 보여주는 스크립트.
 *
 * 우선순위:
 *  1) 안전한 로컬/배포 API(localhost:8000 또는 window.STOCKAGENT_LIVE_API_BASE)가
 *     있으면 그 서버의 /api/portfolio, /api/pnl, /api/state, /api/trades를 직접
 *     읽는다. 이 경로는 업비트 프라이빗 키를 브라우저에 노출하지 않으면서
 *     현금·보유수량·평단·수익률을 모두 실시간으로 갱신한다.
 *  2) API가 없으면 기존처럼 raw.githubusercontent.com의 계좌 스냅샷을 읽고,
 *     coin-live.js의 업비트 웹소켓 시세로 평가액·수익률만 2초마다 재계산한다.
 */
(function () {
  "use strict";

  var SNAPSHOT_POLL_MS = 60000;
  var RENDER_MS = 2000;
  var DIRECT_TABLE_POLL_MS = 10000;
  var DIRECT_PROBE_COOLDOWN_MS = 15000;
  var HTTP_TIMEOUT_MS = 1800;

  var owner = /\.github\.io$/.test(location.hostname)
    ? location.hostname.split(".")[0] : "seank007";
  var RAW_BASE = "https://raw.githubusercontent.com/" + owner + "/stockagent/main/docs/data/";

  var liveApiBase = null;
  var nextProbeAt = 0;
  var directRendering = false;
  var directTradesLoading = false;
  var originalLoadCoinAiTrades = window.loadCoinAiTrades;

  function basePf() { return window.__initialCoinPortfolio; }

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
    try {
      return localStorage.getItem("stockagentLiveApiBase");
    } catch (e) {
      return null;
    }
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

  function apiUrl(base, path) {
    var sep = path.indexOf("?") === -1 ? "?" : "&";
    if (!base) return path + sep + "_live=" + Date.now();
    return base.replace(/\/+$/, "") + path + sep + "_live=" + Date.now();
  }

  function baseLabel(base) {
    return base ? base.replace(/^https?:\/\//, "") : "same-origin";
  }

  function xhrJson(url, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("GET", url, true);
      xhr.timeout = timeoutMs || HTTP_TIMEOUT_MS;
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
        if (data == null) reject(new Error("empty response"));
        else resolve(data);
      };
      xhr.onerror = function () { reject(new Error("network error")); };
      xhr.ontimeout = function () { reject(new Error("timeout")); };
      try { xhr.send(); } catch (e) { reject(e); }
    });
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

  function directFetchJson(url, timeoutMs) {
    var fetchImpl = window.__stockagentNativeFetch || window.fetch;
    if (!fetchImpl) return Promise.reject(new Error("fetch unavailable"));
    var controller = window.AbortController ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs || HTTP_TIMEOUT_MS) : null;
    var init = {
      cache: "no-store",
      credentials: "omit",
      mode: "cors"
    };
    var target = targetAddressSpace(url);
    if (target) init.targetAddressSpace = target;
    if (controller) init.signal = controller.signal;
    return fetchImpl(url, init).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).finally(function () {
      if (timer) clearTimeout(timer);
    });
  }

  function directRequestJson(url, timeoutMs) {
    return directFetchJson(url, timeoutMs).catch(function () {
      return xhrJson(url, timeoutMs);
    });
  }

  function readDirect(path, validate) {
    var bases = liveApiBase ? [liveApiBase] : directApiBases();
    if (!liveApiBase && Date.now() < nextProbeAt) {
      return Promise.reject(new Error("direct API probe cooling down"));
    }
    var idx = 0;
    function tryNext(lastErr) {
      if (idx >= bases.length) {
        liveApiBase = null;
        nextProbeAt = Date.now() + DIRECT_PROBE_COOLDOWN_MS;
        return Promise.reject(lastErr || new Error("direct API unavailable"));
      }
      var base = bases[idx++];
      return directRequestJson(apiUrl(base, path), HTTP_TIMEOUT_MS).then(function (data) {
        if (validate && !validate(data)) throw new Error("invalid payload");
        liveApiBase = base;
        nextProbeAt = 0;
        return data;
      }).catch(tryNext);
    }
    return tryNext();
  }

  function isPortfolioPayload(data) {
    return !!(data && data.summary && Array.isArray(data.holdings));
  }

  function livePriceOf(market) {
    var prices = window.__coinLive && window.__coinLive.prices;
    var v = prices && prices[market];
    return v && v.price > 0 ? Number(v.price) : null;
  }

  function revalueSnapshot() {
    var b = basePf();
    if (!b || !Array.isArray(b.holdings) || !b.holdings.length) return null;
    var p = JSON.parse(JSON.stringify(b));
    var coinValue = 0;
    p.holdings.forEach(function (h) {
      if (h.currency !== "KRW") {
        var live = livePriceOf(h.ticker);
        if (live) {
          h.current_price = live;
          h.current_value = Number(h.balance || 0) * live;
          h.unrealized_pnl = h.current_value - Number(h.principal || 0);
          h.return_pct = Number(h.principal || 0) > 0
            ? (h.current_value / h.principal - 1) * 100 : 0;
        }
        coinValue += Number(h.current_value || 0);
      }
    });
    var s = p.summary || (p.summary = {});
    s.coin_value = coinValue;
    s.total_value = coinValue + Number(s.cash_value || 0);
    s.unrealized_pnl = coinValue + Number(s.cash_value || 0) - Number(s.total_principal || 0);
    s.total_pnl = s.unrealized_pnl + Number(s.realized_pnl || 0);
    s.total_return_pct = Number(s.total_principal || 0) > 0
      ? (s.total_value / s.total_principal - 1) * 100 : 0;
    p.holdings.forEach(function (h) {
      h.weight = s.total_value > 0 ? Number(h.current_value || 0) / s.total_value * 100 : 0;
    });
    return p;
  }

  function aiTabActive() {
    return !document.hidden && window._coinSection === "ai";
  }

  function setNote(pf, mode) {
    var note = document.getElementById("ai-map-note");
    if (!note) return;
    if (pf && pf.account_error) {
      note.textContent = "계좌 조회 오류: " + pf.account_error;
      return;
    }
    if (mode === "direct") {
      var asOf = pf && pf.generated_at ? " · 서버 " + pf.generated_at : "";
      note.textContent = "계좌·수익률 완전 실시간(" + baseLabel(liveApiBase) + ")" + asOf;
      return;
    }
    var snapAt = (basePf() || {}).generated_at;
    note.textContent = "시세·수익률 실시간(웹소켓) · 잔고·거래내역 "
      + (snapAt ? snapAt + " 기준(스냅샷 폴백)" : "스냅샷 기준");
  }

  function renderPortfolio(pf, pnl, mode) {
    if (!pf || !pf.summary) return;
    if (typeof renderAiLiveKpis === "function") renderAiLiveKpis(pf, pnl);
    if (typeof renderAiAccountMap === "function") renderAiAccountMap(pf);
    var upd = document.getElementById("coin-ai-live-upd");
    if (upd) {
      upd.textContent = new Date().toLocaleTimeString("en-GB",
        { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    }
    setNote(pf, mode);
  }

  function renderSnapshotFallback() {
    var p = revalueSnapshot();
    if (!p) return Promise.resolve(false);
    return fetch("/api/pnl").then(function (r) { return r.json(); }).catch(function () { return null; })
      .then(function (pnl) {
        renderPortfolio(p, pnl, "snapshot");
        return true;
      });
  }

  function render() {
    if (!aiTabActive() || directRendering) return;
    directRendering = true;
    readDirect("/api/portfolio", isPortfolioPayload)
      .then(function (pf) {
        window.__initialCoinPortfolio = pf;
        return readDirect("/api/pnl").catch(function () { return null; })
          .then(function (pnl) {
            renderPortfolio(pf, pnl, "direct");
            return true;
          });
      })
      .catch(renderSnapshotFallback)
      .finally(function () { directRendering = false; });
  }

  function loadDirectTrades(force) {
    if (!aiTabActive() || directTradesLoading) return Promise.resolve(false);
    directTradesLoading = true;
    return Promise.all([
      readDirect("/api/state").catch(function () { return null; }),
      readDirect("/api/config").catch(function () { return null; }),
      readDirect("/api/trades?limit=30").catch(function () { return null; })
    ]).then(function (rows) {
      var st = rows[0], cfg = rows[1], tr = rows[2];
      if (!st || !cfg || !tr || typeof renderCoinAiTrades !== "function") {
        throw new Error("direct trade payload unavailable");
      }
      renderCoinAiTrades(st, cfg, tr.items || tr.trades || []);
      var note = document.getElementById("coin-ai-note");
      if (note) {
        note.textContent = "로컬/서버 API 실시간 데이터입니다. 계좌·수익률은 2초, 판단 기록은 10초마다 자동 갱신됩니다.";
        note.style.color = "";
      }
      if (force) render();
      return true;
    }).catch(function () {
      if (typeof originalLoadCoinAiTrades === "function") {
        originalLoadCoinAiTrades(force);
      }
      return false;
    }).finally(function () {
      directTradesLoading = false;
    });
  }

  function fetchJson(url) {
    return fetch(url, { cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function refreshSnapshots() {
    if (document.hidden) return;
    var bust = "?t=" + Date.now();
    Promise.allSettled([
      fetchJson(RAW_BASE + "portfolio_snapshot.json" + bust),
      fetchJson(RAW_BASE + "ai_snapshot.json" + bust)
    ]).then(function (results) {
      var pf = results[0].status === "fulfilled" ? results[0].value : null;
      var ai = results[1].status === "fulfilled" ? results[1].value : null;
      var newer = function (a, b) { return a && (!b || String(a) > String(b)); };
      var changed = false;
      if (pf && Array.isArray(pf.holdings) && pf.holdings.length
          && newer(pf.generated_at, (basePf() || {}).generated_at)) {
        window.__initialCoinPortfolio = pf;
        changed = true;
      }
      if (ai && newer(ai.generated_at, (window.__aiTradeSnapshot || {}).generated_at)) {
        window.__aiTradeSnapshot = ai;
        changed = true;
      }
      if (changed) {
        render();
        if (window._coinSection === "ai") loadDirectTrades(true);
      }
    });
  }

  if (!basePf()) return;

  window.loadCoinAiLive = render;
  window.loadCoinAiTrades = loadDirectTrades;

  function tick() {
    if (aiTabActive()) {
      if (window.__coinLive && typeof window.__coinLive.ensureWs === "function") {
        window.__coinLive.ensureWs();
      }
      render();
    }
  }

  setInterval(tick, RENDER_MS);
  setInterval(function () { loadDirectTrades(false); }, DIRECT_TABLE_POLL_MS);
  setInterval(refreshSnapshots, SNAPSHOT_POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { refreshSnapshots(); tick(); loadDirectTrades(true); }
  });
  tick();
  loadDirectTrades(true);
  refreshSnapshots();
})();
