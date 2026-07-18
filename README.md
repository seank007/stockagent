# stockagent — 업비트 AI 자동매매 + 종목 정보 봇

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/seank007/stockagent)

본인 전용 프로그램. 두 가지 역할을 한다.

1. **주식·코인 자동매매** — 현재는 업비트(코인) 지원
2. **주식/코인 정보 제공** — 종목 분석 리포트·차트

> ⚠️ **암호화폐 자동매매는 원금 손실 위험이 있습니다.** `config.DRY_RUN=True`면 모의매매,
> `False`면 실거래. 실거래는 본인 책임하에 소액으로 검증한 뒤 사용하세요.

## 🚀 SaaS (멀티유저) 모드 지원
이제 단일 사용자 봇을 넘어 **다중 사용자 동시 접속 및 개별 자동매매를 지원하는 SaaS**로 진화했습니다.
- 사용자는 회원가입 후 본인의 업비트 API 키를 등록하여 개별적인 환경에서 모의/자동 매매를 실행할 수 있습니다.
- 보안을 위해 `MULTIUSER_MASTER_KEY`로 사용자 API 시크릿을 강력하게 암호화하며, 출금 권한이 있는 API 키는 등록을 차단합니다.
- 대규모 확장을 위해 SQLite뿐만 아니라 **PostgreSQL** 연결을 기본 지원합니다.

## 구조

```
config.py               설정 (AI제공자, 대상코인, 주기, 한도, DRY_RUN)
brokers/upbit.py        업비트 래퍼 (잔고/시세/지표/주문)
agent/decision.py       매매 판단 (BUY/SELL/HOLD + 신뢰도)
agent/analysis.py       종목 분석 리포트 (정보 제공)
agent/providers/        AI 제공자 (claude / openai / gemini)
risk.py                 리스크 관리 (주문한도·하루손실·신뢰도)
state.py                대시보드용 공유 상태
db.py                   SQLite 영속화 (decisions/trades/positions/daily_pnl)
main.py                 매매 루프 (터미널)
web.py                  웹 대시보드 + 종목 분석 페이지 (브라우저)
backtest.py             백테스트 엔진 (룰/AI 모드)
```

## AI 모델 선택

`config.py`의 `AI_PROVIDER`를 바꾸면 다른 AI로 판단/분석한다 — `.env`에 해당 키만 있으면 됨.

| AI_PROVIDER | 키 | 모델 설정 (config.MODELS) |
|-------------|-----|--------------------------|
| `"claude"` | `ANTHROPIC_API_KEY` | `claude-opus-4-8` 등 |
| `"openai"` | `OPENAI_API_KEY` | `gpt-4o` 등 |
| `"gemini"` | `GEMINI_API_KEY` | `gemini-2.0-flash` 등 |

`OPENAI_BASE_URL`을 설정하면 OpenAI 호환 엔드포인트(OpenRouter, 로컬 LLM 등)도 `"openai"`로 쓸 수 있다.

## 설치

```bash
cd stockagent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 그리고 .env에 키 입력
```

### 키 발급
- **업비트**: [Open API 관리](https://upbit.com/mypage/open_api_management) → Access/Secret key 발급, 주문 권한 + 실행 PC IP 등록
- **Anthropic**: [console.anthropic.com](https://console.anthropic.com)
- **Gemini**: [aistudio.google.com](https://aistudio.google.com/apikey)

## 실행

**웹 대시보드 + 분석 페이지 (추천)**
```bash
python web.py
```
→ `http://localhost:8000`에서 열린다. 자동으로 브라우저를 열려면 `.env`에 `AUTO_OPEN_BROWSER=true`.
- `/` : 매매 루프 상태, PnL 카드, 포트폴리오, 가격/RSI 차트, 판단·거래 기록
- `/analyze` : 임의 KRW-XXX 티커 입력 → 차트 + AI 분석 리포트

**터미널만**
```bash
python main.py
```

**백테스트**
```bash
python backtest.py KRW-BTC --interval minute60 --count 500
python backtest.py KRW-BTC KRW-SOL --mode ai --count 200    # AI 호출 (느리고 비용)
```

**배포 실행**
```bash
python serve.py
```
→ production 서버(Waitress)로 실행한다. Docker 배포와 실거래 전환 절차는
[`DEPLOYMENT.md`](DEPLOYMENT.md)를 참고.

## 공개 배포

GitHub 저장소 주소는 코드가 올라간 곳이고, 그 자체가 실행 중인 사이트는 아니다.
브라우저에서 바로 접속되는 앱 주소는 Render 같은 서버에 배포한 뒤 생성되는
`https://stockagent-....onrender.com` 형태의 URL이다.

- 저장소: https://github.com/seank007/stockagent
- GitHub Pages: https://seank007.github.io/stockagent/
- Render 배포 시작: https://render.com/deploy?repo=https://github.com/seank007/stockagent

기본 배포 설정은 안전하게 `DRY_RUN=true`, `ALLOW_LIVE_TRADING=false`, `AI_PROVIDER=mock`이다.
즉 공개 서버에서 처음 뜨는 앱은 모의/데모 모드이며 업비트 실주문을 넣지 않는다.

## 주요 설정 (config.py)

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `DRY_RUN` | True면 모의매매 | `True` |
| `TICKERS` | 대상 코인 | `["KRW-BTC", "KRW-SOL"]` |
| `INTERVAL_SECONDS` | 판단 주기(초) | `600` |
| `MAX_ORDER_KRW` | 1회 매수 한도 | `10000` |
| `MAX_DAILY_LOSS_KRW` | 하루 손실 한도 | `30000` |
| `MIN_CONFIDENCE` | 매매 최소 신뢰도 | `0.6` |
| `AI_PROVIDER` | 사용할 AI | `"gemini"` |

## 멀티유저(SaaS) 운영 설정 (.env)
본격적인 상용화 및 다중 사용자 운영을 위해 다음 환경변수를 `.env`에 반드시 설정하세요.

| 항목 | 설명 | 예시 |
|------|------|--------|
| `DATABASE_URL` | PostgreSQL DB 주소 (미설정 시 SQLite 사용) | `postgres://user:pass@host:5432/db` |
| `MULTIUSER_MASTER_KEY` | 거래소 키 암호화를 위한 고정 마스터 키 | (안전한 임의 문자열) |
| `SESSION_COOKIE_SECURE` | HTTPS 쿠키 보안 (실제 서비스 시 `true` 필수) | `true` |

## 동작 흐름 (매매 루프)

1. 업비트에서 캔들·시세·잔고 수집
2. 보조지표(이동평균·RSI·변동률) 계산
3. AI가 `{action, confidence, reasoning}` 구조로 반환
4. `risk.py`가 한도/오늘손실/신뢰도 검사 후 주문금액 결정
5. 주문 실행(또는 DRY_RUN) → `db.py`에 판단·거래 기록 → 대기 후 반복

## 영속화

`stockagent.db` (SQLite) 자동 생성. 재시작해도 손익·이력 유지.
- `decisions` : 모든 AI 판단
- `trades` : 체결 기록 + 실현손익
- `positions` : 종목별 보유 수량·평균단가
- `daily_pnl` : 일별 실현손익/매매횟수
