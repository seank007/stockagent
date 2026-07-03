# stockagent 배포 가이드

## 배포 전 체크

1. `.env.example`을 `.env`로 복사하고 실제 값을 채운다.
2. 처음 배포는 반드시 `DRY_RUN=true`로 실행한다.
3. `/healthz`, `/readyz`, 대시보드, 로그를 확인한다.
4. 실거래 전환은 `DRY_RUN=false`와 `ALLOW_LIVE_TRADING=true`를 둘 다 설정한 뒤 재시작한다.

## 로컬/서버 직접 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python serve.py
```

확인:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

## Docker Compose 배포

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
docker compose logs -f stockagent
```

확인:

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/readyz
```

중지:

```bash
docker compose down
```

DB는 `./data/stockagent.db`에 저장된다.

## 실거래 전환

`.env`에서 아래 값이 모두 필요하다.

```dotenv
DRY_RUN=false
ALLOW_LIVE_TRADING=true
UPBIT_ACCESS_KEY=...
UPBIT_SECRET_KEY=...
```

전환 후 재시작:

```bash
docker compose up -d --build
```

## 운영 메모

- production entrypoint는 `python serve.py`다.
- `python web.py`는 개발 실행용이다.
- WSGI 서버는 Waitress를 사용한다.
- `RUN_TRADING_LOOP=false`로 두면 대시보드/분석 API만 띄울 수 있다.
- AI 키가 없거나 쿼터가 막히면 봇은 안전하게 `HOLD`한다.
