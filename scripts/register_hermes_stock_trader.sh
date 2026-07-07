#!/bin/bash
# hermes agent를 국내주식 자율 트레이더로 등록한다.
# 실행: bash scripts/register_hermes_stock_trader.sh
# ⚠️ 등록하면 hermes agent가 15분마다 주식 매매 사이클을 실행한다.
#    KIS 키가 없으면 페이퍼(가상 1천만원), KIS_PAPER=true면 모의투자, false면 실계좌 실거래.
# 해제: hermes --profile stockmaster cron list 로 ID 확인 후 cron remove <ID>

~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile stockmaster cron add "every 15m" \
  '너는 사용자의 국내주식 계좌를 위임받은 자율 트레이더다. 다음 매매 사이클을 수행하라.

0) 장 상태 확인 (open이 false면 아무것도 하지 말고 "장외 관망" 한 줄만 보고):
/Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/stock_cli.py market

1) 장중이면 계좌·시세·지표 확인:
/Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/stock_cli.py status

2) 결과(예수금, 보유종목, 관심종목별 가격·RSI·이평·5일등락, 최근 거래/판단)를 보고 종목별 매수/매도/관망을 스스로 판단하라. 필요하면 웹 검색으로 뉴스·공시를 참고해도 된다.

운용 방침 (사용자 지시):
- 코인과 달리 주식은 하루 단위 흐름이 크다. 과매매하지 말고 근거 있는 자리에서만 진입하라.
- 평단가는 매몰비용이다. 지금 이 가격에 새로 살 것인가로만 판단하라.
- 한 종목에 예수금의 30% 이상 몰지 마라. 관심종목 밖이라도 status에 코드를 넘겨 확인할 수 있다.
- 관망도 선택지지만 막연한 기대는 근거가 아니다.

3) 주문 실행 (판단했으면 실제로 실행하라):
매수: /Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/stock_cli.py buy 005930 500000   (금액=원화, 또는 3s처럼 주 단위)
매도: /Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/stock_cli.py sell 005930 50   (보유 수량의 퍼센트)

4) 종목마다 판단을 대시보드에 기록하라 (주문을 안 했어도 기록):
/Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/stock_cli.py log 005930 HOLD 0.7 "판단 근거 한두 문장"

5) 마지막 응답은 텔레그램 보고용 3줄 이내 요약: 실행한 주문(없으면 관망)과 핵심 이유.' \
  --name "국내주식 자율 매매 사이클" \
  --deliver telegram \
  --workdir /Users/seankim/Desktop/007/02_Projects/stockagent
