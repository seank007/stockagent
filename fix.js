async function openTradeHistoryModal() {
  const modal = document.getElementById("trade-history-modal");
  const rowsContainer = document.getElementById("trade-history-rows");
  if (!modal || !rowsContainer) return;
  modal.hidden = false;
  rowsContainer.innerHTML = "로딩 중..."; // Show loading explicitly

  let res;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 5000); // 5 seconds to be safe

  try {
    res = await fetch("/api/trades?limit=50", { signal: controller.signal });
    if (!res.ok) {
      const txt = await res.text().catch(()=>"");
      clearTimeout(timeoutId);
      rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>HTTP " + res.status + " " + escapeHtml(txt) + "</div>";
      return;
    }
    
    const data = await res.json();
    clearTimeout(timeoutId);
    
    const trades = Array.isArray(data.items) ? data.items : [];
    if (trades.length === 0) {
      rowsContainer.innerHTML = "<div class='muted'>최근 거래 내역이 없습니다.</div>";
      return;
    }
    
    rowsContainer.innerHTML = trades.map(t => {
      const act = String(t.side || "").toUpperCase();
      const cls = act === "BUY" ? "up" : act === "SELL" ? "down" : "muted";
      let time = "—";
      const timeStr = t.ts || t.timestamp;
      if (timeStr) {
        try {
          const d = new Date(timeStr);
          if (isNaN(d.getTime())) {
            time = escapeHtml(String(timeStr));
          } else {
            time = d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
          }
        } catch(e) {
          time = escapeHtml(String(timeStr));
        }
      }
      return `<div style="padding:10px; border-bottom:1px solid #1f2937; display:grid; grid-template-columns: 100px 80px 50px 1fr; gap:10px; align-items:center;">
        <span class="muted" style="font-size:12px;">${time}</span>
        <span style="font-weight:700;">${escapeHtml(t.ticker)}</span>
        <span class="${cls}" style="font-weight:700;">${act}</span>
        <div style="display:flex; flex-direction:column; align-items:flex-end;">
          <span style="font-size:13px; font-weight:700;">${KRW(t.krw_amount || 0)}원</span>
          <span class="muted" style="font-size:11px;">단가 ${KRW(t.price || 0)}원 · 수량 ${NUM(t.volume || 0)}</span>
        </div>
      </div>`;
    }).join("");
    
  } catch (err) {
    clearTimeout(timeoutId);
    rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>" + (err.name === 'AbortError' ? "네트워크 오류(타임아웃 등)" : "스크립트 오류: " + escapeHtml(err.message)) + "</div>";
  }
}
