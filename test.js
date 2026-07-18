const snap = {"trades": [{"id": 75, "ts": "2026-07-05T22:04:30", "ticker": "KRW-BIRB", "side": "sell", "price": 119.0, "volume": 327.2731804545, "krw_amount": 38945.5084740855, "fee": 19.47275423704275, "realized_pnl": 474.4811162849046, "dry_run": 0}]};
const trades = snap.trades;
const KRW = (n, signed=false) => n;
const NUM = (n, digits=6) => n;
function escapeHtml(value) { return value; }

const result = trades.map(t => {
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
console.log(result);
