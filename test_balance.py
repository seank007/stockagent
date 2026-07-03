import os
import pyupbit
from dotenv import load_dotenv

load_dotenv()
access = os.getenv("UPBIT_ACCESS_KEY")
secret = os.getenv("UPBIT_SECRET_KEY")

upbit = pyupbit.Upbit(access, secret)
print("KRW:", upbit.get_balance("KRW"))
print("BTC:", upbit.get_balance("BTC"))
balances = upbit.get_balances()
print("All balances:")
for b in balances:
    print(b)
