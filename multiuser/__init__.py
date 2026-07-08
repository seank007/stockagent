"""stockagent 멀티유저 토대.

기존 단일 사용자 봇(config/db/state/web)을 건드리지 않고, 여러 사용자가 각자
계정으로 로그인해서 자기 거래소 키를 등록/관리할 수 있게 하는 격리된 패키지.

구성
- vault    : 거래소 시크릿을 저장 전 암호화(at-rest)
- db       : 멀티테넌트 SQLite (users / sessions / exchange_credentials)
- accounts : 회원가입·인증·세션·거래소 키 CRUD (공개 API)
- exchange : 업비트 키 유효성 검증 + 출금 권한 키 거부
- web_auth : Flask 인증/키관리 라우트 (web.py에 register(app)로 얹음)
- broker_factory : 사용자별 키로 UpbitBroker 생성

보안 원칙
1. 출금 권한이 있는 거래소 키는 절대 저장하지 않는다(등록 시 검증 후 거부).
2. 시크릿은 평문으로 저장하지 않는다(Fernet 암호화, 실행 순간만 복호화).
3. 모든 사용자 데이터는 user_id로 격리한다.
"""

__all__ = ["vault", "db", "accounts", "exchange"]
