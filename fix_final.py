with open("web.py", "r") as f:
    content = f.read()

content = content.replace('rowsContainer.innerHTML = "로딩 중...";', '// rowsContainer.innerHTML = "로딩 중...";')
content = content.replace('onclick="openTradeHistoryModal()">거래 내역</button>', 'onclick="openTradeHistoryModal()">거래 내역(버그수정판)</button>')

with open("web.py", "w") as f:
    f.write(content)
