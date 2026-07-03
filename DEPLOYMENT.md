# stockagent 배포 가이드

## GitHub 화면과 실제 사이트 차이

`https://github.com/seank007/stockagent`는 코드 저장소다. 여기서 보이는 파일 목록 화면은
정상이며, Flask 앱이 실행 중인 사이트 화면이 아니다.

실제 접속 URL은 서버 배포가 끝난 뒤 Render 대시보드에서 발급되는
`https://stockagent-....onrender.com` 형태의 주소다.

빠른 배포 시작:

```text
https://render.com/deploy?repo=https://github.com/seank007/stockagent
```

현재 `render.yaml`은 공개 데모에 맞춰 `DRY_RUN=true`, `ALLOW_LIVE_TRADING=false`,
`AI_PROVIDER=mock`으로 실행한다. 이 상태에서는 업비트 실거래 주문을 넣지 않는다.

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

## Render 배포

1. GitHub 저장소가 public인지 확인한다.
2. `https://render.com/deploy?repo=https://github.com/seank007/stockagent`를 연다.
3. Render에 로그인하고 Blueprint 배포를 진행한다.
4. 첫 배포가 끝나면 Render 서비스 화면 상단의 `onrender.com` 주소를 연다.
5. `/healthz`가 `ok`를 반환하면 서버가 떠 있는 상태다.

무료 Render 배포는 파일시스템이 영구 저장되지 않는다. 그래서 기본 설정은
`DB_PATH=/tmp/stockagent.db`를 쓰며, 재배포/재시작 때 로컬 SQLite 기록이 사라질 수 있다.
실제 매매 기록을 장기 보존하려면 유료 디스크, 외부 DB, 또는 본인 서버/VPS 배포가 필요하다.

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
- 업비트 실거래는 API 키, 주문 권한, IP 허용 목록이 모두 맞아야 한다. Render 무료/공유 환경은
  고정 IP 운영에 적합하지 않을 수 있으므로 실거래는 로컬 머신이나 고정 IP 서버에서 먼저 검증한다.
