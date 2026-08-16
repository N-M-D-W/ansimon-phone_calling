#!/usr/bin/env python3
"""안심온 — 입력된 번호로 ClawOps 예방 안내전화 1건 발신.

    pip install "clawops[agent,openai]"
    python voice/call.py 01012345678

범위: 발신까지. 결과 구조화/Spring 콜백/재시도는 CLAWOPS_PHONE_WORKFLOW Phase 4~5.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from clawops import ClawOps
from clawops.agent import BuiltinTool, ClawOpsAgent
from clawops.agent.plugins.openai_realtime import OpenAIRealtime

from job import load
from report import CALLS, FAREWELL, ending, report

MAX_CALL_SEC = 180   # 목표 60~90초. 넘으면 우리가 끊는다.

# 목소리: alloy ash ballad coral echo sage shimmer verse marin cedar
# STT/TTS를 따로 교체하는 분리형(PipelineSession)은 CLAWOPS_PHONE_WORKFLOW.txt §10 참고.
VOICE = "marin"

# guidance_json 키 이름은 백엔드 guidance 패키지의 DTO 가 아직 없어 미확정이다.
# 아는 키는 한국어 라벨을 붙이고, 모르는 키는 키 이름 그대로 내보낸다.
# 이렇게 해야 Spring 이 키를 바꿔도 통화가 죽지 않는다.
LABEL = {
    "risk_window": "더워지는 시간",
    "message": "예방 행동",
    "recommendation": "권장 사항",
}


def facts(job: dict) -> str:
    """[사실] 블록. 없는 항목은 줄째로 뺀다 — 빈 값을 읽어주면 안 된다."""
    G, S = job.get("guidance") or {}, job.get("shelter") or {}
    lines = [f"- {LABEL.get(k, k)}: {v}" for k, v in G.items()
             if isinstance(v, str) and v.strip()]
    if name := S.get("name"):
        walk = f", 걸어서 {S['walk_minutes']}분" if S.get("walk_minutes") else ""
        lines.append(f"- 추천 쉼터: {name}{walk}")
    if not lines:
        raise ValueError("guidance/shelter 에 안내할 내용이 없다 — 발신 중단")
    return "\n".join(lines)


def prompt(job: dict) -> str:
    """job 마다 새로 만든다. 한 프로세스가 여러 건을 처리하므로 전역이면 안 된다."""
    return f"""너는 '안심온' AI 안부확인 상담원이다. 상대는 고령의 어르신이다.

첫 문장: "안녕하세요. 안심온 AI 안부확인 서비스입니다.
현재 폭염 위험이 높아 안전 확인과 예방 안내를 위해 연락드렸습니다."

규칙:
- 짧고 느린 한국어. 한 번에 질문 하나만.
- 아래 [사실] 밖의 숫자·주소·시간을 절대 만들지 않는다.
- 못 알아들으면 한 번만 쉬운 말로 다시 묻고, 두 번째도 불명확하면 넘어간다.
- 의료 진단을 하지 않는다. 긴급 증상을 말하면 안내를 멈추고
  담당자에게 전달하겠다고 말한 뒤 종료한다.

답변 확인 규칙 (매우 중요)
- 어르신의 답을 반대로 바꿔 말하지 않는다. 들은 그대로만 되읽는다.
  "네 있어요" 는 가능하다는 뜻이다. 이를 "어렵다고 들었습니다" 로 바꾸지 않는다.
- 어르신이 정정하면 정정한 내용을 최종 답으로 삼는다.

마무리 규칙
- 통화를 끝내는 유일한 방법은 end_call 함수를 호출하는 것이다.
- end_call 은 아래 순서를 모두 마친 뒤에만 호출한다.
    1) 아래 질문의 답을 모두 듣는다
    2) 들은 답을 한 문장으로 정리해 확인한다
    3) "담당 사회복지사에게 전달하겠습니다. 안녕히 계세요" 라고 말한다
- 위 인사말을 말하기 전에 end_call 을 부르면 거부된다.
  거부되면 끊지 말고 남은 절차를 이어서 진행한다.
- "잠시만요" 처럼 말을 끊고 멈추지 않는다. 할 말은 한 번에 끝낸다.
- 어르신이 새로운 요청을 하시면 먼저 응답한 뒤 절차를 이어간다.

[사실]  ← 이 값들만 말한다. intervention_plan 이 확정한 내용이다.
{facts(job)}

순서: 통화 가능한지 확인 → 위 사실 안내 → 아래 질문을 하나씩
""" + "\n".join(f"{i}. {q}" for i, q in enumerate(job["questions"], 1)) + """
→ 들은 답을 한 문장으로 정리해 확인 → 마무리 인사 → end_call 호출.
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


async def main(job: dict, to: str) -> dict:
    agent = ClawOpsAgent(
        from_=from_number(),
        session=OpenAIRealtime(system_prompt=prompt(job), language="ko", voice=VOICE),
        machine_detection="Enable",  # 자동응답기 → NO_ANSWER
        recording=False,             # 워크플로우 §6: 녹취 미저장
        # SDK 기본 hang_up 은 설명이 "대화가 끝나면 끊어라" 라 제약이 안 걸린다.
        # 대신 아래 end_call 을 직접 등록하고 통과 조건을 코드로 검사한다.
        builtin_tools=BuiltinTool.NONE,
    )
    turns: list[dict] = []
    end_reason: str | None = None

    # agent 레벨에 등록해야 한다. CallSession은 agent.call() 안에서 만들어지면서
    # 곧바로 prewarm이 시작되므로, 반환값에 거는 방식은 초반 발화를 놓친다.
    @agent.on("transcript")
    async def _(_session, role: str, text: str) -> None:
        """STT — Realtime이 양쪽 발화를 실시간으로 준다. 녹음 불필요."""
        print(f"  {'어르신' if role == 'user' else 'AI    '} │ {text}", flush=True)
        turns.append({"role": role, "text": text})

    @agent.tool
    async def end_call() -> str:
        """통화를 종료한다. 마무리 인사를 마친 뒤에만 호출할 것."""
        # 게이트는 프롬프트가 아니라 실제 전사로 판정한다.
        # 모델이 인사했다고 주장해도 전사에 없으면 통과되지 않는다.
        if not any(FAREWELL in t["text"] for t in turns if t["role"] == "assistant"):
            print("  · end_call 거부 (마무리 인사 없음)", flush=True)
            return (f"아직 마무리 인사를 하지 않았습니다. 들은 답을 정리해 확인하고 "
                    f"'담당 사회복지사에게 전달하겠습니다. {FAREWELL}' 라고 말한 뒤 다시 호출하세요.")
        print("  · end_call 승인", flush=True)
        await call.hangup()
        return "통화를 종료했습니다."

    print(f"발신 → {mask(to)}\n──── 통화 내용 ────", flush=True)
    try:
        call = await agent.call(to, timeout=60)  # connect()는 내부에서 자동 수행
        try:
            await asyncio.wait_for(call.wait(), timeout=MAX_CALL_SEC)
        except asyncio.TimeoutError:
            print(f"  ! {MAX_CALL_SEC}초 초과 — 이쪽에서 종료", flush=True)
            await call.hangup()
        m = call.metrics  # duration·metrics는 메서드가 아니라 property
        end_reason = m.end_reason
        print(f"──────────────────\n종료 · {call.duration:.0f}초 · 사유 {m.end_reason}"
              f" · 첫 응답 {m.first_response_ms}ms · 끼어들기 {m.barge_in_count}회")
    finally:
        await agent.disconnect()
        if not turns:
            print("\n말이 하나도 오가지 않았습니다. 위 로그에서 다음을 확인하세요:\n"
                  "  · 'OpenAI Realtime connected' 가 있는가 → 없으면 OpenAI 연결 실패\n"
                  "  · 'PREWARM-T failed' / 'invalid_api_key' → 키·크레딧 문제\n"
                  "  진단: python scripts/check_openai.py", flush=True)
        if turns:  # ponytail: PoC용 로컬 저장. DB 에는 전사를 넣지 않는다
            CALLS.mkdir(exist_ok=True)
            out = CALLS / f"{to}_{datetime.now():%Y%m%d_%H%M%S}.json"
            out.write_text(json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"대화 {len(turns)}턴 → {out.name}")

    r = await report(turns, job, provider_call_id=call.call_id, end_reason=end_reason)
    print(f"\n판정 · 쉼터의향 {r['shelter_intent']} · 혼자이동 {r['can_move_alone']}"
          f" · 도움필요 {r['help_needed']} · 증상언급 {r['symptom_mentioned']}")
    print(f"종료유형 · {ending(turns, end_reason)}")
    print(f"요약 · {r['summary']}")
    return r


def setup() -> None:
    """SDK 내부 로그를 보이게 한다. 이게 없으면 OpenAI Realtime 연결 실패가
    조용히 넘어가서 '전화는 되는데 말을 안 한다' 로만 보인다."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("clawops").setLevel(logging.INFO)
    load_dotenv()


if __name__ == "__main__":
    assert mask("01012345678") == "*******5678"
    setup()
    JOB = load()
    # 번호는 인자 우선, 없으면 job 의 to_number (DB 가 아닌 별도 보관소 값)
    to = sys.argv[1] if len(sys.argv) > 1 else JOB.get("to_number")
    if not to:
        sys.exit("사용법: python voice/call.py 01012345678")
    asyncio.run(main(JOB, to))
