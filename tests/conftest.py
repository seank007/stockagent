"""테스트 공통 설정: 실제 계좌/DB를 건드리지 않도록 임시 DB·모의 모드로 고정."""
import os
import tempfile

# config/db import 전에 환경을 잡아둔다.
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="stockagent-test-"), "test.db"))
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("WEB_AUTH_TOKEN", "")
