import os
import pyupbit
from dotenv import load_dotenv


def main() -> None:
    """Explicit, manual account connectivity check (never runs during pytest import)."""
    load_dotenv()
    access = os.getenv("UPBIT_ACCESS_KEY", "").strip()
    secret = os.getenv("UPBIT_SECRET_KEY", "").strip()
    if not access or not secret:
        raise SystemExit("UPBIT_ACCESS_KEY와 UPBIT_SECRET_KEY를 먼저 설정하세요.")

    upbit = pyupbit.Upbit(access, secret)
    print("KRW:", upbit.get_balance("KRW"))
    print("BTC:", upbit.get_balance("BTC"))
    balances = upbit.get_balances()
    print("All balances:")
    for balance in balances:
        print(balance)


if __name__ == "__main__":
    main()
