"""stockagent 설정.

환경변수(.env)에서 키를 읽고, 매매 동작 파라미터를 한곳에 모은다.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return [item.strip().upper() for item in value.split(",") if item.strip()]


# --- API 키 ---
UPBIT_ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY", "")
UPBIT_SECRET_KEY = os.getenv("UPBIT_SECRET_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# OpenAI 호환 엔드포인트(OpenRouter/로컬 등)를 쓸 때만 설정. 비우면 OpenAI 기본 주소.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")

# --- 어떤 AI로 판단할지 ---
# "mock"(키 없이 데모) | "claude" | "openai" | "gemini"
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()

# provider별 사용할 모델명 (본인 계정에서 쓸 수 있는 모델로 바꿔도 됨)
MODELS = {
    "claude": os.getenv("CLAUDE_MODEL", "claude-opus-4-8"),
    "openai": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "gemini": os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
}

EFFORT = os.getenv("EFFORT", "medium")  # claude 전용: low | medium | high | max

# --- 안전장치 ---
# True면 실제 주문을 내지 않고 "이렇게 매매했을 것"이라는 로그만 남긴다(모의매매).
# 실거래는 .env에 DRY_RUN=false와 ALLOW_LIVE_TRADING=true를 둘 다 명시해야 한다.
DRY_RUN = _env_bool("DRY_RUN", True)
ALLOW_LIVE_TRADING = _env_bool("ALLOW_LIVE_TRADING", False)

# --- 대상 종목 ---
TICKERS = _env_list("TICKERS", ["KRW-BTC", "KRW-SOL"])

# --- 매매 루프 ---
INTERVAL_SECONDS = _env_int("INTERVAL_SECONDS", 600)
CANDLE_INTERVAL = os.getenv("CANDLE_INTERVAL", "minute60")
CANDLE_COUNT = _env_int("CANDLE_COUNT", 50)

# --- 주문/리스크 한도 ---
MAX_ORDER_KRW = _env_int("MAX_ORDER_KRW", 10_000)
MIN_ORDER_KRW = _env_int("MIN_ORDER_KRW", 5_000)
MAX_DAILY_LOSS_KRW = _env_int("MAX_DAILY_LOSS_KRW", 30_000)
MIN_CONFIDENCE = _env_float("MIN_CONFIDENCE", 0.6)

# --- 성능 튜닝 ---
# 종목별 시세 수집 + AI 판단을 병렬 처리할 최대 작업자 수.
MAX_DECISION_WORKERS = _env_int("MAX_DECISION_WORKERS", 4)
# AI provider가 쿼터/인증/네트워크 오류를 내면 이 시간 동안 건너뛰고 다음 provider를 먼저 쓴다.
AI_PROVIDER_COOLDOWN_SECONDS = _env_int("AI_PROVIDER_COOLDOWN_SECONDS", 600)
AI_HTTP_TIMEOUT_SECONDS = _env_int("AI_HTTP_TIMEOUT_SECONDS", 12)
# 대시보드 폴링 payload가 커지지 않게 긴 판단 근거/에러 전문을 잘라서 보낸다.
STATE_HISTORY_LIMIT = _env_int("STATE_HISTORY_LIMIT", 80)
STATE_REASON_MAX_CHARS = _env_int("STATE_REASON_MAX_CHARS", 260)
API_REASON_MAX_CHARS = _env_int("API_REASON_MAX_CHARS", 360)

# --- 서버/배포 ---
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = _env_int("WEB_PORT", _env_int("PORT", 8000))
WEB_THREADS = _env_int("WEB_THREADS", 8)
RUN_TRADING_LOOP = _env_bool("RUN_TRADING_LOOP", True)
AUTO_OPEN_BROWSER = _env_bool("AUTO_OPEN_BROWSER", False)


def validate() -> None:
    """선택한 provider의 키와, 실거래라면 업비트 키가 있는지 확인."""
    keymap = {
        "claude": ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        "openai": ("OPENAI_API_KEY", OPENAI_API_KEY),
        "gemini": ("GEMINI_API_KEY", GEMINI_API_KEY),
    }
    provider = AI_PROVIDER.lower()

    missing = []
    # mock은 키가 필요 없음 (데모용)
    if provider != "mock":
        if provider not in keymap:
            raise SystemExit(f"AI_PROVIDER 값이 잘못됨: {AI_PROVIDER!r}")
        name, val = keymap[provider]
        if not val or _looks_placeholder_key(val):
            missing.append(name)
    if not DRY_RUN:
        if not UPBIT_ACCESS_KEY or _looks_placeholder_key(UPBIT_ACCESS_KEY):
            missing.append("UPBIT_ACCESS_KEY")
        if not UPBIT_SECRET_KEY or _looks_placeholder_key(UPBIT_SECRET_KEY):
            missing.append("UPBIT_SECRET_KEY")
        if not ALLOW_LIVE_TRADING:
            missing.append("ALLOW_LIVE_TRADING=true")
    if missing:
        raise SystemExit(
            "배포 설정이 부족합니다: " + ", ".join(missing) + "\n.env 파일을 확인하세요."
        )


def _looks_placeholder_key(value: str) -> bool:
    text = str(value or "").strip().lower()
    markers = ("your-", "your_", "placeholder", "example", "changeme", "api-key")
    return any(marker in text for marker in markers)
