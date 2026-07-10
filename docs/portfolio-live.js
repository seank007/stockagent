// GitHub Pages 포트폴리오 실시간 평가.
// 로컬/배포 API가 있으면 현금·보유수량·평단·수익률을 모두 직접 갱신하고,
// API가 없을 때만 스냅샷 보유분 + 업비트 공개 시세 재평가로 폴백한다.
(() => {
  const getBase = () => window.__initialCoinPortfolio;
  if (!getBase() || !Array.isArray(getBase().holdings) || !getBase().holdings.length) return;

  const DIRECT_POLL_MS = 2000;
  const FALLBACK_POLL_MS = 10000;
  const PROBE_COOLDOWN_MS = 15000;
  const HTTP_TIMEOUT_MS = 1800;
  let liveApiBase = null;
  let nextProbeAt = 0;
  let loading = false;
  let lastPayload = getBase();

  function uniquePush(rows, value) {
    if (value == null) return;
    value = String(value).trim().replace(/\/+$/, "");
    if (!value && value !== "") return;
    if (!rows.includes(value)) rows.push(value);
  }

  function configuredApiBase() {
    try {
      const params = new URLSearchParams(location.search || "");
      const fromQuery = params.get("liveApi") || params.get("api");
      if (fromQuery) {
        localStorage.setItem("stockagentLiveApiBase", fromQuery);
        return fromQuery;
      }
    } catch (e) {}
    if (window.STOCKAGENT_LIVE_API_BASE) return window.STOCKAGENT_LIVE_API_BASE;
    try { return localStorage.getItem("stockagentLiveApiBase"); } catch (e) { return null; }
  }

  function directApiBases() {
    const bases = [];
    uniquePush(bases, configuredApiBase());
    if (!/\.github\.io$/.test(location.hostname) && /^https?:$/.test(location.protocol)) {
      uniquePush(bases, location.origin);
    }
    uniquePush(bases, "http://127.0.0.1:8000");
    uniquePush(bases, "http://localhost:8000");
    return bases;
  }

  function apiUrl(base, path) {
    const sep = path.includes("?") ? "&" : "?";
    return (base ? base.replace(/\/+$/, "") : "") + path + sep + "_live=" + Date.now();
  }

  function baseLabel(base) {
    return base ? base.replace(/^https?:\/\//, "") : "same-origin";
  }

  function xhrJson(url) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("GET", url, true);
      xhr.timeout = HTTP_TIMEOUT_MS;
      xhr.responseType = "json";
      xhr.onload = () => {
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error("HTTP " + xhr.status));
          return;
        }
        let data = xhr.response;
        if (data == null && xhr.responseText) {
          try { data = JSON.parse(xhr.responseText); } catch (e) {}
        }
        data == null ? reject(new Error("empty response")) : resolve(data);
      };
      xhr.onerror = () => reject(new Error("network error"));
      xhr.ontimeout = () => reject(new Error("timeout"));
      try { xhr.send(); } catch (e) { reject(e); }
    });
  }

  function targetAddressSpace(url) {
    try {
      const host = new URL(url, location.href).hostname.toLowerCase();
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
    const fetchImpl = window.__stockagentNativeFetch || window.fetch;
    if (!fetchImpl) return Promise.reject(new Error("fetch unavailable"));
    const controller = window.AbortController ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS) : null;
    const init = {
      cache: "no-store",
      credentials: "omit",
      mode: "cors"
    };
    const target = targetAddressSpace(url);
    if (target) init.targetAddressSpace = target;
    if (controller) init.signal = controller.signal;
    return fetchImpl(url, init).then(res => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).finally(() => {
      if (timer) clearTimeout(timer);
    });
  }

  function directRequestJson(url) {
    return directFetchJson(url).catch(() => xhrJson(url));
  }

  function readDirectPortfolio() {
    const bases = liveApiBase ? [liveApiBase] : directApiBases();
    if (!liveApiBase && Date.now() < nextProbeAt) {
      return Promise.reject(new Error("direct API probe cooling down"));
    }
    let idx = 0;
    const tryNext = lastErr => {
      if (idx >= bases.length) {
        liveApiBase = null;
        nextProbeAt = Date.now() + PROBE_COOLDOWN_MS;
        return Promise.reject(lastErr || new Error("direct API unavailable"));
      }
      const base = bases[idx++];
      return directRequestJson(apiUrl(base, "/api/portfolio")).then(data => {
        if (!data || !data.summary || !Array.isArray(data.holdings)) {
          throw new Error("invalid portfolio payload");
        }
        liveApiBase = base;
        nextProbeAt = 0;
        return data;
      }).catch(tryNext);
    };
    return tryNext();
  }

  async function revalueSnapshot() {
    const base = getBase();
    const markets = base.holdings
      .map(h => h.ticker)
      .filter(t => typeof t === "string" && t.startsWith("KRW-"));
    if (!markets.length) throw new Error("보유 코인 없음");
    const res = await fetch("https://api.upbit.com/v1/ticker?markets=" + markets.join(","));
    if (!res.ok) throw new Error("업비트 시세 HTTP " + res.status);
    const tickers = await res.json();
    const prices = {};
    for (const t of tickers) prices[t.market] = Number(t.trade_price);

    const p = JSON.parse(JSON.stringify(base));
    let coinValue = 0;
    for (const h of p.holdings) {
      const live = prices[h.ticker];
      if (live > 0) {
        h.current_price = live;
        h.current_value = Number(h.balance || 0) * live;
        h.unrealized_pnl = h.current_value - Number(h.principal || 0);
        h.return_pct = Number(h.principal || 0) > 0
          ? (h.current_value / h.principal - 1) * 100 : 0;
      }
      if (h.currency !== "KRW") coinValue += Number(h.current_value || 0);
    }
    const s = p.summary || (p.summary = {});
    s.coin_value = coinValue;
    s.total_value = coinValue + Number(s.cash_value || 0);
    s.unrealized_pnl = coinValue + Number(s.cash_value || 0) - Number(s.total_principal || 0);
    s.total_pnl = s.unrealized_pnl + Number(s.realized_pnl || 0);
    s.total_return_pct = Number(s.total_principal || 0) > 0
      ? (s.total_value / s.total_principal - 1) * 100 : 0;
    for (const h of p.holdings) {
      h.weight = s.total_value > 0 ? Number(h.current_value || 0) / s.total_value * 100 : 0;
    }
    lastPayload = p;
    return p;
  }

  function setNote(text) {
    const note = document.getElementById("coin-pf-note");
    if (note) note.textContent = text;
  }

  async function liveLoad() {
    if (loading || document.hidden || (window._coinSection && window._coinSection !== "portfolio")) return;
    loading = true;
    try {
      const p = await readDirectPortfolio();
      window.__initialCoinPortfolio = p;
      lastPayload = p;
      if (typeof renderCoinPortfolio === "function") renderCoinPortfolio(p);
      setNote("계좌·수익률 완전 실시간(" + baseLabel(liveApiBase) + ")"
        + (p.generated_at ? " · 서버 " + p.generated_at : ""));
    } catch (e) {
      try {
        const p = await revalueSnapshot();
        if (typeof renderCoinPortfolio === "function") renderCoinPortfolio(p);
        setNote("시세·수익률 실시간 · 수량/현금은 "
          + (getBase().generated_at || "스냅샷") + " 기준");
      } catch (fallbackError) {
        if (typeof renderCoinPortfolio === "function") renderCoinPortfolio(lastPayload);
      }
    } finally {
      loading = false;
    }
  }

  window.loadCoinPortfolio = liveLoad;
  setInterval(liveLoad, DIRECT_POLL_MS);
  setInterval(() => { if (!liveApiBase) liveLoad(); }, FALLBACK_POLL_MS);
  if (!window._coinSection || window._coinSection === "portfolio") liveLoad();
})();
