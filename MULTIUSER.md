# 멀티유저 전환 (stockagent → 공개 서비스)

혼자 쓰던 자동매매 봇을, 여러 사용자가 각자 계정으로 로그인해 **자기 거래소 키를
직접 등록**하고 자기 계정을 자동화하는 서비스로 만들기 위한 토대.

> ⚠️ **법률 먼저.** 불특정 다수를 대상으로 남의 돈을 재량 운용(투자일임)하려면
> 금융위 인가가 필요하고, 무인가 시 형사처벌 대상이다. 현실적으로 개인이 갈 수 있는
> 길은 **"사용자가 본인 키로 본인 계정을 자동화하는 도구(SaaS)"** 모델이다. 그래도
> 자동매매 대행·수익 보장 광고는 회색지대라 **오픈 전 자본시장법 전문 변호사 자문은 필수.**
> 이 코드는 그 전제(사용자 본인 키, 출금 권한 없음) 위에서 설계됐다.

## 지금까지 만든 것 (보안 핵심 토대) ✅

기존 단일 봇(`config`/`db`/`state`/`web.py`)을 **건드리지 않고** 격리된 `multiuser/`
패키지로 추가했다. 기존 봇은 그대로 동작한다.

| 파일 | 역할 |
|---|---|
| `multiuser/vault.py` | 거래소 시크릿을 Fernet으로 **암호화 저장**(평문 금지). 마스터 키는 env 주입. |
| `multiuser/db.py` | 멀티테넌트 SQLite(`multiuser.db`): `users` / `sessions` / `exchange_credentials`. |
| `multiuser/exchange.py` | 업비트 키 **유효성 검증 + 출금 권한 키 거부**(핵심 안전장치). |
| `multiuser/accounts.py` | 회원가입·로그인·세션·거래소 키 CRUD 공개 API. 비번은 해시로만 저장. |
| `multiuser/web_auth.py` | Flask 인증/키관리 라우트(`/auth/*`). httpOnly 세션 쿠키. |
| `multiuser/broker_factory.py` | 사용자별 복호화 키로 `UpbitBroker` 생성(전역 config와 격리). |
| `tests/test_multiuser.py` | vault·인증·세션·**출금키 거부**·유저 격리 검증(14 tests). |

### 3대 보안 원칙 (코드로 강제됨)
1. **출금 권한 키 거부** — 등록 시 `/v1/withdraws/chance`로 확인, 출금 가능하면 저장 자체를 막음.
2. **시크릿 평문 저장 금지** — DB엔 암호문만, 복호화는 주문 실행 순간만.
3. **유저 격리** — 모든 조회가 `user_id` 기준. 남의 키/데이터 접근 불가.

## web.py에 붙이는 법 (선택 — 아직 자동 적용 안 함)

```python
# web.py 상단, app = Flask(__name__) 다음에
import multiuser.web_auth as web_auth
web_auth.register(app)   # /auth/register, /auth/login, /auth/me, /auth/credentials ...
```

배포 전 환경변수(`.env`):
```
MULTIUSER_MASTER_KEY=<generate_master_key()로 생성한 값>   # 운영 필수
SESSION_COOKIE_SECURE=true                                # HTTPS 운영
```

## API 요약

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/auth/register` | `{email, password}` → 가입 + 세션 쿠키 |
| POST | `/auth/login` | `{email, password}` → 세션 쿠키 |
| POST | `/auth/logout` | 세션 폐기 |
| GET | `/auth/me` | 현재 사용자 |
| GET | `/auth/credentials` | 등록한 키 목록(마스킹, 시크릿 노출 안 함) |
| POST | `/auth/credentials` | `{access_key, secret_key}` → **검증 후** 저장(출금키 거부) |
| DELETE | `/auth/credentials` | `{label}` 키 삭제 |

## 남은 단계 (로드맵)

- [ ] **1. 프론트엔드** — 로그인/회원가입 화면, 키 등록 폼(출금 권한 빼라는 안내 + IP 화이트리스트 가이드).
- [ ] **2. 거래/판단 이력 멀티테넌트화** — `db.py`의 `decisions/trades/positions/daily_pnl`에 `user_id` 추가, 모든 쿼리 필터. 현재는 단일 봇용 전역 테이블.
- [ ] **3. 유저별 상태 저장소** — `state.py`의 전역 `store` 싱글턴 → 유저별 인스턴스(dict[user_id]).
- [ ] **4. 유저별 거래 엔진** — 유저당 워커/큐로 격리 실행(`broker_factory.broker_for_user`로 생성). 한 명 에러·rate limit이 남에게 안 번지게.
- [ ] **5. 인프라** — 개인 맥 launchd → 클라우드(서버 + Postgres 이관 검토) + 모니터링/백업/장애복구.
- [ ] **6. 법무/운영** — 약관·면책·개인정보처리방침(손실은 사용자 책임), 변호사 자문, 2FA.

## 테스트

```bash
~/.venvs/stockagent/bin/python -m pytest tests/test_multiuser.py -q
```
