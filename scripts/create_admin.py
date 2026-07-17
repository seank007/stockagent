#!/usr/bin/env python3
"""Create or promote a multiuser administrator from a trusted shell."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from multiuser import accounts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a stockagent administrator")
    parser.add_argument("email", help="Email listed in ADMIN_EMAILS")
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args()

    token = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "")
    if not token:
        raise SystemExit("ADMIN_BOOTSTRAP_TOKEN 환경변수를 먼저 설정하세요.")
    password = getpass.getpass("관리자 비밀번호: ")
    confirm = getpass.getpass("관리자 비밀번호 확인: ")
    if password != confirm:
        raise SystemExit("비밀번호가 일치하지 않습니다.")

    try:
        user = accounts.bootstrap_admin(
            args.email,
            password,
            token,
            display_name=args.display_name,
        )
    except accounts.AccountError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"관리자 준비 완료: {user['email']}")


if __name__ == "__main__":
    main()
