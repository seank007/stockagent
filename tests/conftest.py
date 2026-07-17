"""테스트 공통 설정: 실제 계좌/DB를 건드리지 않도록 임시 DB·모의 모드로 고정."""
import os
import tempfile

# config/db import 전에 환경을 잡아둔다.
_TEST_DIR = tempfile.mkdtemp(prefix="stockagent-test-")
os.environ.update({
    "PYTHON_DOTENV_DISABLED": "true",
    "DB_PATH": os.path.join(_TEST_DIR, "coin.db"),
    "STOCK_DB_PATH": os.path.join(_TEST_DIR, "stock.db"),
    "MULTIUSER_DB_PATH": os.path.join(_TEST_DIR, "multiuser.db"),
    "DRY_RUN": "true",
    "ALLOW_LIVE_TRADING": "false",
    "AI_PROVIDER": "mock",
    "WEB_AUTH_TOKEN": "",
})
