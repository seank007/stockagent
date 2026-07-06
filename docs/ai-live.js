/*
 * ai-live.js — GitHub Pages에서 "AI 거래" 탭의 계좌를 실시간으로 보여주는 스크립트.
 *
 * 두 층으로 실시간을 만든다:
 *  1) 시세·수익률: coin-live.js가 유지하는 업비트 웹소켓 시세(window.__coinLive)로
 *     보유코인 평가액·수익률·총자산을 2초마다 브라우저에서 재계산한다.
 *  2) 잔고·거래내역: 업비트 프라이빗 API는 브라우저에서 호출할 수 없으므로,
 *     로컬 봇(scripts/live_sync.py)이 거래 발생 시 push하는 최신 스냅샷 JSON을
 *     raw.githubusercontent.com에서 60초마다 다시 받아 반영한다(Pages 재배포 대기 없음).
 *
 * 정적 목업 fetch 오버라이드·coin-live.js 다음에 로드된다.
 */
(function () {
  "use strict";

  var SNAPSHOT_POLL_MS = 60000;
  var RENDER_MS = 2000;

  // Pages 호스트(seank007.github.io/stockagent)에서 raw 콘텐츠 주소를 유도한다.
  var owner = /\.github\.io$/.test(location.hostname)
    ? location.hostname.split(".")[0] : "seank007";
  var RAW_BASE = "https://raw.githubusercontent.com/" + owner + "/stockagent/main/docs/data/";

  function basePf() { return window.__initialCoinPortfolio; }

  function livePriceOf(market) {
    var prices = window.__coinLive && window.__coinLive.prices;
    var v = prices && prices[market];
    return v && v.price > 0 ? Number(v.price) : null;
  }

  // 스냅샷 잔고 + 웹소켓 실시간가 → 평가액·수익률·비중·총계 재계산
  function revalue() {
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

  var rendering = false;
  function render() {
    if (!aiTabActive() || rendering) return;
    var p = revalue();
    if (!p) return;
    rendering = true;
    // /api/pnl 목업 라우트는 최신 스냅샷 전역에서 오늘 거래·실현손익을 계산한다.
    fetch("/api/pnl").then(function (r) { return r.json(); }).catch(function () { return null; })
      .then(function (pnl) {
        try {
          if (typeof renderAiLiveKpis === "function") renderAiLiveKpis(p, pnl);
          if (typeof renderAiAccountMap === "function") renderAiAccountMap(p);
          var upd = document.getElementById("coin-ai-live-upd");
          if (upd) {
            upd.textContent = new Date().toLocaleTimeString("ko-KR",
              { hour: "2-digit", minute: "2-digit", second: "2-digit" });
          }
          var note = document.getElementById("ai-map-note");
          if (note) {
            var asOf = (basePf() || {}).generated_at;
            note.textContent = "시세·수익률 실시간(웹소켓) · 잔고·거래내역 "
              + (asOf ? asOf + " 기준(자동 갱신)" : "스냅샷 기준");
          }
        } catch (e) { /* 렌더 함수 미로드 등은 다음 틱에 재시도 */ }
        rendering = false;
      });
  }

  // ---- 최신 스냅샷 폴링: 거래·잔고 변경을 재배포 없이 반영 --------------------
  function fetchJson(url) {
    return fetch(url, { cache: "no-store" }).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  var polling = false;
  function refreshSnapshots() {
    if (document.hidden || polling) return;
    polling = true;
    var bust = "?t=" + Date.now();
    Promise.allSettled([
      fetchJson(RAW_BASE + "portfolio_snapshot.json" + bust),
      fetchJson(RAW_BASE + "ai_snapshot.json" + bust)
    ]).then(function (results) {
      var pf = results[0].status === "fulfilled" ? results[0].value : null;
      var ai = results[1].status === "fulfilled" ? results[1].value : null;
      // generated_at("YYYY-MM-DD HH:MM")은 문자열 비교가 시간 비교와 같다.
      // 페이지에 심어진 것보다 "더 최신"일 때만 교체한다(오래된 캐시로 후퇴 방지).
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
        // 거래 내역·AI 판단 테이블도 새 스냅샷으로 다시 그린다.
        if (window._coinSection === "ai" && typeof loadCoinAiTrades === "function") {
          try { loadCoinAiTrades(true); } catch (e) { /* 무시 */ }
        }
      }
      polling = false;
    });
  }

  // ---- 기동 ------------------------------------------------------------------
  if (!basePf()) return; // 스냅샷 없는 페이지(주식 등)에서는 아무것도 안 함

  // 기존 7초 정적 루프(loadCoinAiLive)를 실시간 버전으로 교체
  window.loadCoinAiLive = render;

  function tick() {
    if (aiTabActive()) {
      if (window.__coinLive && typeof window.__coinLive.ensureWs === "function") {
        window.__coinLive.ensureWs();
      }
      render();
    }
  }
  setInterval(tick, RENDER_MS);
  setInterval(refreshSnapshots, SNAPSHOT_POLL_MS);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { refreshSnapshots(); tick(); }
  });
  tick();
  refreshSnapshots();
})();
