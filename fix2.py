import sys

with open("web.py", "r", encoding="utf-8") as f:
    content = f.read()

# Fix modal class
content = content.replace('class="modal-overlay"', 'class="modal"')

old_func = """async function openTradeHistoryModal() {
  try {
    const modal = document.getElementById("trade-history-modal");
    const rowsContainer = document.getElementById("trade-history-rows");
    if (!modal || !rowsContainer) return;
    modal.hidden = false;
    // rowsContainer.innerHTML = "로딩 중...";

    let res;
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 3000);
      res = await fetch("/api/trades?limit=50", { signal: controller.signal });
      clearTimeout(timeoutId);
    } catch (e) {
      rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>네트워크 오류(타임아웃 등): " + escapeHtml(e.message) + "</div>";
      return;
    }

    if (!res.ok) {
      const txt = await res.text().catch(()=>"");
      rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>HTTP " + res.status + " " + escapeHtml(txt) + "</div>";
      return;
    }

    const data = await res.json();
    const trades = data.items || [];
    if (trades.length === 0) {
      rowsContainer.innerHTML = "<div class='muted'>최근 거래 내역이 없습니다.</div>";
      return;
    }
    rowsContainer.innerHTML = trades.map(t => {
      const act = String(t.side || "").toUpperCase();
      const cls = act === "BUY" ? "up" : act === "SELL" ? "down" : "muted";
      const time = t.ts ? new Date(t.ts).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : (t.timestamp ? new Date(t.timestamp).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
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
    const rows = document.getElementById("trade-history-rows");
    if (rows) rows.innerHTML = "<div class='muted' style='color:#ff8a93'>스크립트 오류: " + escapeHtml(err.message) + "</div>";
  }
}"""

new_func = """async function openTradeHistoryModal() {
  const modal = document.getElementById("trade-history-modal");
  const rowsContainer = document.getElementById("trade-history-rows");
  if (!modal || !rowsContainer) return;
  modal.hidden = false;
  rowsContainer.innerHTML = "로딩 중..."; // Show loading explicitly

  let res;
  let timeoutId;
  try {
    const controller = new AbortController();
    timeoutId = setTimeout(() => controller.abort(), 5000); // 5 seconds to be safe
    
    try {
      res = await fetch("/api/trades?limit=50", { signal: controller.signal });
    } catch (e) {
      clearTimeout(timeoutId);
      rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>네트워크 오류(타임아웃 등): " + escapeHtml(e.message) + "</div>";
      return;
    }
    
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
    if (timeoutId) clearTimeout(timeoutId);
    rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>" + (err.name === 'AbortError' ? "네트워크 오류(타임아웃 등)" : "스크립트 오류: " + escapeHtml(err.message)) + "</div>";
  }
}"""

# Convert to python f-string escaping
old_func_escaped = old_func.replace("{", "{{").replace("}", "}}")
new_func_escaped = new_func.replace("{", "{{").replace("}", "}}")

if old_func_escaped in content:
    content = content.replace(old_func_escaped, new_func_escaped)
    with open("web.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Replaced successfully")
else:
    print("Could not find old_func_escaped in web.py")
