#!/usr/bin/env python3
"""안심온 — 입력된 번호로 ClawOps 예방 안내전화 1건 발신.

    pip install "clawops[agent,openai]"
    python voice/call.py 01012345678

범위: 발신까지. 결과 구조화/Spring 콜백/재시도는 CLAWOPS_PHONE_WORKFLOW Phase 4~5.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from clawops import ClawOps
from clawops.agent import ClawOpsAgent
from clawops.agent.plugins.openai_realtime import OpenAIRealtime

# Spring이 승인한 interventionPlan (워크플로우 §2.1). 지금은 데모 고정값.
PLAN = {
    "riskWindow": "오늘 오후 한 시부터 다섯 시까지",
    "guidance": ["오후 한 시 전부터 시원한 실내에 머물러 주세요.", "물을 조금씩 자주 드세요."],
    "shelter": "OO경로당 무더위쉼터, 걸어서 팔 분",
}

PROMPT = f"""너는 '안심온' AI 안부확인 상담원이다. 상대는 고령의 어르신이다.

첫 문장: "안녕하세요. 안심온 AI 안부확인 서비스입니다.
현재 폭염 위험이 높아 안전 확인과 예방 안내를 위해 연락드렸습니다."

규칙:
- 짧고 느린 한국어. 한 번에 질문 하나만.
- 아래 [사실] 밖의 숫자·주소·시간을 절대 만들지 않는다.
- 못 알아들으면 한 번만 쉬운 말로 다시 묻고, 두 번째도 불명확하면 넘어간다.
- 의료 진단을 하지 않는다. 긴급 증상을 말하면 안내를 멈추고
  담당자에게 전달하겠다고 말한 뒤 종료한다.

[사실]
- 더워지는 시간: {PLAN["riskWindow"]}
- 예방 행동: {" / ".join(PLAN["guidance"])}
- 추천 쉼터: {PLAN["shelter"]}

순서: 통화 가능한지 확인 → 위 사실 안내 → 아래 질문을 하나씩
1. 쉼터에 가실 의향이 있으신가요?
2. 혼자 이동하실 수 있으신가요?
3. 이동이나 다른 도움이 필요하신가요?
→ 들은 답을 한 문장으로 되읽어 확인 → 담당자에게 전달하겠다고 알리고 종료.
"""


def mask(number: str) -> str:
    """로그용 마스킹 — 뒤 4자리만 남긴다 (워크플로우 §6)."""
    return "*" * max(len(number) - 4, 0) + number[-4:]


def from_number() -> str:
    if n := os.getenv("CLAWOPS_FROM_NUMBER"):
        return n
    owned = list(ClawOps().numbers.list())
    if not owned:
        sys.exit("보유한 070 번호가 없습니다: python scripts/provision_number.py --create")
    return owned[0].number


async def main(to: str) -> None:
    agent = ClawOpsAgent(
        from_=from_number(),
        session=OpenAIRealtime(system_prompt=PROMPT, language="ko"),
        machine_detection="Enable",  # 자동응답기 → NO_ANSWER
        recording=False,             # 워크플로우 §6: 녹취 미저장
    )
    print(f"발신 → {mask(to)}")
    try:
        call = await agent.call(to, timeout=60)  # connect()는 내부에서 자동 수행
        await call.wait()
        print(f"종료 · {call.duration()}초 · {call.metrics()}")
    finally:
        await agent.disconnect()


if __name__ == "__main__":
    assert mask("01012345678") == "*******5678"
    load_dotenv()
    if len(sys.argv) != 2:
        sys.exit("사용법: python voice/call.py 01012345678")
    asyncio.run(main(sys.argv[1]))
