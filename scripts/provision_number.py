#!/usr/bin/env python3
"""안심온 — ClawOps 070 번호 조회 / 발급.

    python scripts/provision_number.py            # 보유 번호 조회
    python scripts/provision_number.py --create   # 없으면 발급
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from clawops import ClawOps

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

if not os.getenv("OPENAI_API_KEY"):
    print("! OPENAI_API_KEY 미설정 — 번호는 발급되지만 통화는 안 됩니다.", file=sys.stderr)

# 키가 없거나 잘못되면 ClawOps() 가 한국어로 알려준다.
client = ClawOps()
numbers = list(client.numbers.list())

for n in numbers:
    print(n.number)

if not numbers:
    if "--create" not in sys.argv:
        sys.exit("보유한 번호가 없습니다. 발급: python scripts/provision_number.py --create")
    new = client.numbers.create()
    print(f"발급 완료: {new.number}")
    print(f".env 에 넣으세요 →  CLAWOPS_FROM_NUMBER={new.number}")
