with open("web.py", "r") as f:
    content = f.read()

target = """  try {
    const res = await fetch("/api/trades?limit=50");
    if (!res.ok) throw new Error("HTTP " + res.status + " " + await res.text());"""

target_escaped = target.replace("{", "{{").replace("}", "}}")

replacement = """  let res;
  try {{
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3000);
    res = await fetch("/api/trades?limit=50", {{ signal: controller.signal }});
    clearTimeout(timeoutId);
  }} catch (e) {{
    rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>네트워크 오류(타임아웃 등): " + escapeHtml(e.message) + "</div>";
    return;
  }}
  
  if (!res.ok) {{
    const txt = await res.text().catch(()=>"");
    rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>HTTP " + res.status + " " + escapeHtml(txt) + "</div>";
    return;
  }}"""

if target_escaped in content:
    content = content.replace(target_escaped, replacement)
    # Also fix timestamp to ts
    content = content.replace('const time = t.timestamp ? new Date(t.timestamp).toLocaleString("ko-KR"',
                              'const time = t.ts ? new Date(t.ts).toLocaleString("ko-KR", {{ month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }}) : (t.timestamp ? new Date(t.timestamp).toLocaleString("ko-KR"')
    
    # Change the catch logic
    old_catch = """  }} catch (err) {{
    rowsContainer.innerHTML = "<div class='muted' style='color:#ff8a93'>거래 내역을 불러오는데 실패했습니다: " + escapeHtml(err.message) + "</div>";
  }}"""
    new_catch = """  }} catch (err) {{
    const rows = document.getElementById("trade-history-rows");
    if (rows) rows.innerHTML = "<div class='muted' style='color:#ff8a93'>스크립트 오류: " + escapeHtml(err.message) + "</div>";
  }}"""
    content = content.replace(old_catch, new_catch)
    
    # Finally, make sure the button has V2 and loading is commented
    content = content.replace('rowsContainer.innerHTML = "로딩 중...";', '// rowsContainer.innerHTML = "로딩 중...";')
    content = content.replace('onclick="openTradeHistoryModal()">거래 내역</button>', 'onclick="openTradeHistoryModal()">거래 내역 (V2)</button>')

    with open("web.py", "w") as f:
        f.write(content)
    print("SUCCESS")
else:
    print("TARGET NOT FOUND")
