#!/usr/bin/env python3
"""
안심온 — ClawOps 070 번호 발급 / 확인

이 스크립트 하나로 세 가지를 합니다:
  1. .env 를 읽어 ClawOps 키가 제대로 설정됐는지 검사
  2. 이미 보유한 070 번호 목록 조회
  3. 번호가 없으면 새로 발급 (--create)

사용:
    pip install clawops
    python scripts/provision_number.py            # 검사 + 목록만
    python scripts/provision_number.py --create   # 없으면 새로 발급
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
OK, FAIL, WARN = "\033[92mOK\033[0m", "\033[91mFAIL\033[0m", "\033[93mWARN\033[0m"


def load_env() -> dict:
    """.env 를 읽어 os.environ 에 넣는다 (= 앞뒤 공백은 제거)."""
    if not ENV_PATH.exists():
        sys.exit(f"{FAIL} .env 가 없습니다: {ENV_PATH}")
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        env[k] = v
        if v:
            os.environ[k] = v
    return env


def check(env: dict) -> None:
    print("[1] 환경변수 점검")

    # 흔한 실수: CLAWOPS_API 로 적어두는 경우 (SDK는 CLAWOPS_API_KEY 만 읽음)
    if "CLAWOPS_API" in env and not env.get("CLAWOPS_API_KEY"):
        print(f"  {FAIL} 변수명이 CLAWOPS_API 입니다. SDK는 "
              f"CLAWOPS_API_KEY 만 읽습니다. 이름을 바꾸세요.")
        sys.exit(1)

    problems = False
    for var, hint in [
        ("CLAWOPS_API_KEY", "sk_ 로 시작"),
        ("CLAWOPS_ACCOUNT_ID", "AC 로 시작 · platform.claw-ops.com 대시보드에서 확인"),
    ]:
        val = env.get(var, "")
        if not val:
            print(f"  {FAIL} {var} 미설정 ({hint})")
            problems = True
        else:
            print(f"  {OK} {var} = {val[:6]}…{val[-4:]}")

    if env.get("CLAWOPS_API_KEY", "").startswith("sk-"):
        print(f"  {WARN} 값이 'sk-' 로 시작합니다. OpenAI 키를 잘못 넣은 것 같습니다 "
              f"(ClawOps는 'sk_' 언더스코어).")

    if not env.get("OPENAI_API_KEY"):
        print(f"  {WARN} OPENAI_API_KEY 미설정 — 번호 발급은 되지만 통화는 안 됩니다.")

    if problems:
        sys.exit("\n필수 변수를 채운 뒤 다시 실행하세요.")


def main() -> None:
    env = load_env()
    check(env)

    from clawops import ClawOps  # 검사 후에 import (에러 메시지를 우리가 먼저 내기 위해)

    client = ClawOps()

    print("\n[2] 보유 번호 조회")
    try:
        numbers = list(client.numbers.list())
    except Exception as e:
        sys.exit(f"  {FAIL} 조회 실패: {type(e).__name__}: {e}\n"
                 f"       키가 유효한지, 계정이 활성 상태인지 확인하세요.")

    if numbers:
        for n in numbers:
            print(f"  {OK} {getattr(n, 'phone_number', n)}")
    else:
        print("  보유한 번호가 없습니다.")

    if not numbers and "--create" in sys.argv:
        print("\n[3] 새 070 번호 발급")
        try:
            new = client.numbers.create()
            print(f"  {OK} 발급 완료: {new.phone_number}")
            print(f"\n  .env 에 아래 줄을 넣으세요:")
            print(f"      CLAWOPS_FROM_NUMBER={new.phone_number}")
        except Exception as e:
            sys.exit(f"  {FAIL} 발급 실패: {type(e).__name__}: {e}")
    elif not numbers:
        print("\n  발급하려면: python scripts/provision_number.py --create")

    if env.get("CLAWOPS_FROM_NUMBER"):
        print(f"\n현재 발신번호 설정: {env['CLAWOPS_FROM_NUMBER']}")
        print("다음 단계: python voice/ansimon_call.py 01012345678")


if __name__ == "__main__":
    main()
