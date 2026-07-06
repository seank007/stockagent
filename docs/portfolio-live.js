// GitHub Pages 포트폴리오 실시간 평가.
// 보유 수량·평단은 배포 시점 스냅샷(window.__initialCoinPortfolio) 기준이고,
// 시세·평가액·손익은 업비트 공개 API(CORS 허용)로 10초마다 다시 계산한다.
(() => {
  // ai-live.js가 최신 스냅샷으로 전역을 갱신하므로 매번 다시 읽는다.
  const getBase = () => window.__initialCoinPortfolio;
  if (!getBase() || !Array.isArray(getBase().holdings) || !getBase().holdings.length) return;

  let lastPayload = getBase();

  async function revalue() {
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

  async function liveLoad() {
    if (document.hidden || (window._coinSection && window._coinSection !== "portfolio")) return;
    try {
      const p = await revalue();
      if (typeof renderCoinPortfolio === "function") renderCoinPortfolio(p);
      const note = document.getElementById("coin-pf-note");
      if (note && !note.textContent.includes("실시간")) {
        note.textContent += " · 수량은 " + (getBase().generated_at || "배포 시점") + " 기준, 시세는 업비트 실시간";
      }
    } catch (e) {
      // 시세 조회가 잠깐 실패하면 마지막 값 유지
      if (typeof renderCoinPortfolio === "function") renderCoinPortfolio(lastPayload);
    }
  }

  // 페이지 내장 loadCoinPortfolio(정적 스냅샷 반환)를 실시간 버전으로 교체
  window.loadCoinPortfolio = liveLoad;
  setInterval(liveLoad, 10000);
  if (!window._coinSection || window._coinSection === "portfolio") liveLoad();
})();
