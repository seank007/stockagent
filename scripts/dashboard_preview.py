"""대시보드 헤더(로그인 버튼) 미리보기용. mock/dry_run이라 키 없이 뜬다."""
import os, sys
os.environ.setdefault("AI_PROVIDER", "mock")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("ALLOW_LIVE_TRADING", "false")
os.environ.setdefault("RUN_TRADING_LOOP", "false")
os.environ.setdefault("MULTIUSER_DB_PATH", "/tmp/dash_mu.db")
os.environ.setdefault("MULTIUSER_MASTER_KEY_FILE", "/tmp/dash_mu.key")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from web import app
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8011, debug=False)
