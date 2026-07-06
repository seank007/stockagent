"""거래·잔고 변경 감지 → GitHub Pages 스냅샷 자동 배포 데몬.

로컬 봇(localhost:8000)의 계좌·거래·AI 판단을 주기적으로 확인해서, 마지막으로
push한 상태와 달라졌을 때만 scripts/export_github_pages.py 를 실행해 docs/ 를
재생성하고 커밋 + push 한다. push 되면:
 - raw.githubusercontent.com 의 스냅샷 JSON이 즉시 갱신되어 Pages의 ai-live.js가
   60초 폴링으로 새 잔고·거래내역을 반영하고,
 - GitHub Actions(pages.yml)가 docs/** 변경으로 재배포된다.

사용:
  python scripts/live_sync.py            # 1회 확인 후 종료
  python scripts/live_sync.py --force    # 변경 없어도 export+push
  python scripts/live_sync.py --loop     # 45초 간격 상주(launchd용)

설치(로그인 시 자동 시작): scripts/install_live_sync.sh
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = "http://localhost:8000"
STATE_FILE = Path.home() / ".stockagent_live_sync.json"
LOOP_SECONDS = 45
# Pages 배포가 연속으로 몰리면 "Deployment failed, try again later"가 나므로
# push 사이 최소 간격을 둔다. 그 사이 변경은 다음 push에 묶여 나간다.
MIN_PUSH_INTERVAL = 150


def log(msg: str) -> None:
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


def _get(path: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(BOT + path, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def signature() -> dict | None:
    """봇 API 기준 '의미 있는 변경' 서명. 시세 변동은 포함하지 않는다."""
    try:
        pf = _get("/api/portfolio")
        trades = _get("/api/trades?limit=1")
        decisions = _get("/api/decisions?limit=1")
    except Exception as exc:  # noqa: BLE001 - 봇 미가동 등
        log(f"봇 API 조회 실패, 이번 주기 건너뜀: {exc}")
        return None

    holdings = sorted(
        (
            str(h.get("ticker")),
            round(float(h.get("balance") or 0), 8),
            round(float(h.get("avg_buy_price") or 0), 6),
        )
        for h in pf.get("holdings") or []
    )
    trade_items = trades.get("items") or trades.get("trades") or []
    decision_items = decisions.get("items") or decisions.get("decisions") or []
    return {
        "holdings": holdings,
        "last_trade": (trade_items[0].get("id"), trade_items[0].get("ts")) if trade_items else None,
        "last_decision": decision_items[0].get("ts") if decision_items else None,
    }


def load_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_state(sig: dict) -> None:
    STATE_FILE.write_text(json.dumps(sig, ensure_ascii=False, default=str), encoding="utf-8")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def export_and_push() -> bool:
    log("스냅샷 export 시작")
    result = run([sys.executable, str(ROOT / "scripts" / "export_github_pages.py")],
                 timeout=600)
    if result.returncode != 0:
        log(f"export 실패:\n{result.stderr[-2000:]}")
        return False

    run(["git", "add", "docs"])
    diff = run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        log("docs 변경 없음 (커밋 생략)")
        return True

    stamp = datetime.now().strftime("%m-%d %H:%M")
    commit = run(["git", "commit", "-m",
                  f"chore: live snapshot {stamp} (auto live_sync)\n\n"
                  "Co-Authored-By: stockagent live_sync <noreply@local>"])
    if commit.returncode != 0:
        log(f"커밋 실패:\n{commit.stderr[-1000:]}")
        return False

    for attempt in (1, 2):
        push = run(["git", "push", "origin", "main"], timeout=120)
        if push.returncode == 0:
            log("push 완료 → Pages 재배포 트리거됨")
            return True
        log(f"push 실패(시도 {attempt}): {push.stderr.strip()[-500:]}")
        if attempt == 1:
            pull = run(["git", "pull", "--rebase", "origin", "main"], timeout=120)
            if pull.returncode != 0:
                run(["git", "rebase", "--abort"])
                log("rebase 실패 — 다음 주기에 재시도")
                return False
    return False


_last_push_ts = 0.0


def cycle(force: bool = False) -> None:
    global _last_push_ts
    sig = signature()
    if sig is None:
        return
    state = load_state() or {}
    if not force and sig == state.get("sig"):
        return  # 거래·잔고·판단 변화 없음
    since_push = time.time() - max(_last_push_ts, float(state.get("pushed_ts") or 0))
    if not force and since_push < MIN_PUSH_INTERVAL:
        return  # 변경은 있지만 직전 push와 너무 가까움 — 다음 주기에 묶어서
    log("변경 감지" if not force else "강제 실행")
    if export_and_push():
        _last_push_ts = time.time()
        save_state({"sig": sig, "pushed_ts": _last_push_ts})


def main() -> None:
    args = set(sys.argv[1:])
    if "--loop" in args:
        log(f"live_sync 상주 시작 (주기 {LOOP_SECONDS}s, repo={ROOT})")
        while True:
            try:
                cycle(force=False)
            except Exception as exc:  # noqa: BLE001 - 데몬은 죽지 않는다
                log(f"주기 중 오류: {exc}")
            time.sleep(LOOP_SECONDS)
    else:
        cycle(force="--force" in args)


if __name__ == "__main__":
    main()
