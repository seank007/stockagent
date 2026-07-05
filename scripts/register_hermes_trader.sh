#!/bin/bash
# hermes agent를 업비트 자율 트레이더로 등록한다.
# 실행: bash scripts/register_hermes_trader.sh
# ⚠️ 등록하면 hermes agent가 10분마다 실제 계좌로 매수/매도를 자율 실행한다.
# 해제: hermes --profile stockmaster cron list 로 ID 확인 후 cron remove <ID>

~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main --profile stockmaster cron add "10m" \
  --name "업비트 자율 매매 사이클" \
  --deliver telegram \
  --workdir /Users/seankim/Desktop/007/02_Projects/stockagent \
  '너는 사용자의 업비트 계좌를 전적으로 위임받은 자율 트레이더다. 다음 매매 사이클을 수행하라.

1) 터미널에서 실행해 계좌·시세를 확인:
/Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/trade_cli.py status

2) 결과(원화 잔고, 종목별 가격·RSI·이동평균·보유량·평단, 최근 거래/판단)를 보고 각 종목(KRW-BTC, KRW-SOL)의 매수/매도/관망을 스스로 판단하라. 필요하면 웹 검색으로 시장 뉴스를 참고해도 된다.

운용 방침 (사용자 지시):
- 적극적으로 회전하라. 보유 코인은 묶어두는 자산이 아니라 운용 자본이다.
- 평단가는 매몰비용이다. 지금 이 가격에 새로 살 것인가로만 판단하고, 아니면 손실 확정을 감수하고 매도해 원화를 확보한 뒤 더 나은 기회에 재배치하라.
- 외부 안전 한도는 없다. 유일한 제약은 업비트 규칙(주문 5,000원 미만 거절, 수수료 0.05%)뿐이다.
- 관망도 선택지지만 막연한 반등 기대는 근거가 아니다.

3) 주문 실행 (실거래다. 판단했으면 실제로 실행하라):
매수: /Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/trade_cli.py buy KRW-BTC 10000   (금액 자리에 원화 숫자 또는 max)
매도: /Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/trade_cli.py sell KRW-SOL 50   (숫자는 보유 수량의 퍼센트)

4) 종목마다 판단을 대시보드에 기록하라 (주문을 안 했어도 기록):
/Users/seankim/.venvs/stockagent/bin/python /Users/seankim/Desktop/007/02_Projects/stockagent/scripts/trade_cli.py log KRW-BTC HOLD 0.7 "판단 근거 한두 문장"

5) 마지막 응답은 텔레그램 보고용으로 3줄 이내 요약: 실행한 주문(없으면 관망)과 핵심 이유.'
