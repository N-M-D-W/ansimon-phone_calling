#!/usr/bin/env python3
"""통화 전사(STT) -> 구조화 결과 -> Spring 전송.

계약은 CLAWOPS_PHONE_WORKFLOW.txt §2.2 / §2.3 을 그대로 따른다.
LLM 교체(OpenAI -> Alan)는 summarize() 한 곳만 고치면 된다.

단독 실행으로 저장된 전사를 다시 정리할 수 있다:
    python voice/report.py 01012345678   # 그 번호의 가장 최근 통화
    python voice/report.py               # 전체 중 가장 최근 통화
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
YNU = {"YES", "NO", "UNKNOWN"}


def now() -> str:
    """DATETIME(6) 에 들어갈 시각 문자열.

    오프셋(+09:00)을 붙이지 않는다. DB 컬럼이 DATETIME(6) 이라 시간대를 저장하지
    못하고, 자바 LocalDateTime 은 오프셋이 붙은 문자열을 파싱하지 못한다.
    모든 시각은 Asia/Seoul 기준이다(백엔드 global 규약과 동일).
    """
    return datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S.%f")

# 전사 파일: calls/{수신번호}_{YYYYMMDD_HHMMSS}.json
# 시각이 고정폭이라 사전순 정렬 = 시간순 정렬. 별도 색인이 필요 없다.
CALLS = Path(__file__).parent / "calls"

FAREWELL = "안녕히 계세요"  # AI 가 이 말을 했으면 안내를 끝까지 마친 것

# contact_status 로 보낼 수 있는 값. 백엔드 ContactStatus enum 에 있는 것만 쓴다.
# 주의: DB 설명서에는 NO_ANSWER 라고 적혀 있으나 enum 에 그 값이 없다.
#       미응답은 UNCONFIRMED 다 (contact/AGENTS.md 의 "미응답 → 재전화/수동확인" 흐름).
STATUS = {"ANSWERED": "ANSWERED", "NO_ANSWER": "UNCONFIRMED", "ERROR": "FAILED"}

# 통화가 어떻게 끝났는지. 사회복지사가 요약보다 먼저 봐야 하는 정보다.
# summary 가 NOT NULL 이라 최소한 이 문구는 항상 들어간다.
ENDING_NOTE = {
    "NO_ANSWER": "[전화를 받지 않으셨습니다] ",
    "USER_HUNG_UP_EARLY": "[어르신이 안내 도중 먼저 끊으셨습니다] ",
    "TIMEOUT": "[시간 초과로 통화를 종료했습니다] ",
    "ERROR": "[통신 오류로 통화가 끊겼습니다] ",
}


def ending(turns: list[dict], end_reason: str | None) -> str:
    """COMPLETED / NO_ANSWER / USER_HUNG_UP_EARLY / TIMEOUT / ERROR.

    end_reason 문자열은 SDK 벤더 값이라 믿을 수 있는 게 error 뿐이다.
    나머지는 우리가 직접 본 전사로 판정한다.
    """
    if any(FAREWELL in t["text"] for t in turns if t["role"] == "assistant"):
        return "COMPLETED"          # 인사까지 했으면 누가 끊었든 정상
    if end_reason == "error":
        return "ERROR"
    if not turns:
        return "NO_ANSWER"          # 한 마디도 오가지 않음 = 미연결·자동응답기
    if end_reason == "user_hangup":
        return "USER_HUNG_UP_EARLY"
    return "TIMEOUT"                # 인사 없이 우리 쪽에서 끊긴 경우


def latest(phone: str | None = None) -> Path:
    """번호별(또는 전체) 가장 최근 전사 파일."""
    found = sorted(CALLS.glob(f"{phone or '*'}_*.json"))
    if not found:
        sys.exit(f"전사가 없습니다: {CALLS}/{phone or '*'}_*.json")
    return found[-1]

# call_observation 테이블 컬럼명 그대로 쓴다. 값은 반드시 YES/NO/UNKNOWN.
FIELDS = {
    "shelter_intent": "무더위쉼터에 갈 의향이 있다고 했는가",
    "can_move_alone": "쉼터까지 혼자 이동할 수 있다고 했는가",
    "help_needed": "이동이나 다른 도움이 필요하다고 했는가",
    "symptom_mentioned": "어지럼증·두통·구역질·의식저하 등 온열질환 의심 증상을 언급했는가",
}

SYSTEM = f"""너는 폭염 안부확인 통화 기록을 정리하는 분석기다.

아래 항목을 판단해 JSON 하나만 출력한다. 설명이나 코드블록을 붙이지 않는다.

{chr(10).join(f'- {k}: {v}' for k, v in FIELDS.items())}
- summary: 담당 사회복지사가 읽을 2~3문장 한국어 요약
- confidence: 판단 확신도 0.0~1.0

절대 규칙
- 각 항목의 값은 "YES", "NO", "UNKNOWN" 중 하나다.
- 어르신이 명확히 답하지 않은 항목은 반드시 "UNKNOWN"으로 둔다. 추측하지 않는다.
- 의료 진단을 하지 않는다. 증상 언급 여부만 기록한다.
- 대화에 없는 사실을 요약에 넣지 않는다.

출력 형식
{{"shelter_intent":"...","can_move_alone":"...","help_needed":"...",
  "symptom_mentioned":"...","summary":"...","confidence":0.0}}"""


# ─────────────────────────────────────────────────────────────
# LLM — 여기만 갈아끼우면 Alan 등 다른 제공자로 교체된다.
#       입력: 대화 텍스트 / 출력: FIELDS + summary + confidence 를 담은 dict
# ─────────────────────────────────────────────────────────────
async def summarize(dialogue: str) -> dict:
    from openai import AsyncOpenAI

    r = await AsyncOpenAI().chat.completions.create(
        model=os.getenv("SUMMARY_MODEL", "gpt-4o"),
        response_format={"type": "json_object"},
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": dialogue},
        ],
    )
    return json.loads(r.choices[0].message.content)


def coerce(raw: dict) -> dict:
    """LLM 출력을 계약 enum 으로 강제. 제공자를 바꿔도 이 방어는 유지한다."""
    out = {k: (raw.get(k) if raw.get(k) in YNU else "UNKNOWN") for k in FIELDS}
    out["summary"] = str(raw.get("summary") or "")[:1000]   # VARCHAR(1000)
    try:
        out["confidence"] = round(min(max(float(raw.get("confidence", 0)), 0.0), 1.0), 4)  # DECIMAL(5,4)
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    return out


def build_result(contact_job_id: int, analysis: dict | None,
                 status: str, note: str = "") -> dict:
    """call_observation 테이블에 그대로 INSERT 되는 모양으로 만든다.

    전사 원문은 넣지 않는다. DB 에 저장할 컬럼이 없고, 넣어서도 안 된다.
    """
    base = {k: "UNKNOWN" for k in FIELDS} | {"summary": "", "confidence": 0.0}
    body = dict(analysis or base)
    body["summary"] = (note + body["summary"])[:1000]  # 중단 사유를 맨 앞에
    return {
        "contact_job_id": contact_job_id,
        "contact_status": status,      # STATUS 의 값만 (ContactStatus.java)
        **body,
        "ended_at": now(),
    }


async def analyze(turns: list[dict], contact_job_id: int, note: str = "") -> dict:
    """전사 -> 결과 본문. 어르신 발화가 없으면 LLM 을 부르지 않는다."""
    if not any(t["role"] == "user" for t in turns):
        # note 를 흘리지 않는다. summary 가 NOT NULL 인데 빈 문자열이면
        # 사회복지사 화면에 아무 단서도 안 남는다.
        return build_result(contact_job_id, None, STATUS["NO_ANSWER"], note)
    dialogue = "\n".join(
        f"{'어르신' if t['role'] == 'user' else 'AI'}: {t['text']}" for t in turns
    )
    try:
        analysis = coerce(await summarize(dialogue))
    except Exception as e:  # LLM 실패로 통화 결과를 잃지 않는다
        print(f"  ! 요약 실패 ({type(e).__name__}) — UNKNOWN 으로 전송", file=sys.stderr)
        return build_result(contact_job_id, None, STATUS["ANSWERED"], note)
    return build_result(contact_job_id, analysis, STATUS["ANSWERED"], note)


async def send(observation: dict, meta: dict) -> None:
    """Spring 전송. 멱등키를 헤더로 넘긴다.

    경로는 백엔드 AGENTS.md 규약: 내부 배치/콜백은 /internal/v1/**.
    (외부 공개용 /api/v1/** 이 아니다.)
    """
    base = os.getenv("ANSIMON_BACKEND_BASE_URL")
    body = {"observation": observation, "meta": meta}
    if not base:
        print("\n=== Spring 전송 예정 payload (BASE_URL 미설정) ===")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return

    import aiohttp

    url = f"{base.rstrip('/')}/internal/v1/contact-jobs/{observation['contact_job_id']}/observation"
    # contact_job.idempotency_key 만 쓰면 안 된다. 재시도는 같은 contact_job 의
    # attempt_count 만 올리므로 키가 그대로라, 1차 미응답 결과와 2차 통화 결과가
    # 같은 키로 들어가 2차가 중복으로 버려진다. 시도 회차를 붙여 구분한다.
    headers = {"Idempotency-Key": f"{meta['idempotency_key']}:{meta['attempt_count']}"}
    if tok := os.getenv("ANSIMON_BACKEND_TOKEN"):
        headers["Authorization"] = f"Bearer {tok}"

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=10) as r:
            # 백엔드 공통 응답 {success, data, error} — 실패 사유를 삼키지 않는다
            err = (await r.json()).get("error") if r.content_type == "application/json" else None
            print(f"Spring 전송 {r.status} {url}" + (f" · error={err}" if err else ""))


async def report(turns: list[dict], job: dict, provider_call_id: str | None = None,
                 end_reason: str | None = None) -> dict:
    """call.py 에서 부르는 진입점. job 은 contact_job 한 건.

    observation = call_observation 행 (그대로 INSERT 가능)
    meta        = DB 컬럼이 아닌 운영 정보. Spring 이 contact_job 갱신·분기에 사용
    """
    how = ending(turns, end_reason)
    observation = await analyze(turns, job["contact_job_id"], ENDING_NOTE.get(how, ""))
    if how == "ERROR":
        observation["contact_status"] = STATUS["ERROR"]
    meta = {
        "provider": "CLAWOPS",
        # 원본 전화번호는 보내지 않는다. DB 는 phone_hash 만 저장하고,
        # 대상자 식별은 elderly_id 로 한다.
        "elderly_id": job["elderly_id"],
        "intervention_plan_id": job["intervention_plan_id"],
        "attempt_count": job["attempt_count"],
        "idempotency_key": job["idempotency_key"],
        "call_ending": how,          # COMPLETED / USER_HUNG_UP_EARLY / TIMEOUT / ERROR
        "end_reason": end_reason,    # SDK 원본 값
        "provider_call_id": provider_call_id,
        "received_at": now(),
    }
    await send(observation, meta)
    return observation


if __name__ == "__main__":
    # 저장된 전사를 다시 정리 (LLM 프롬프트 튜닝용)
    assert coerce({"shelter_intent": "maybe"})["shelter_intent"] == "UNKNOWN"
    assert coerce({"confidence": 5})["confidence"] == 1.0
    from dotenv import load_dotenv

    load_dotenv()
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    path = Path(arg) if arg and not arg.isdigit() else latest(arg)
    print(f"대상 전사: {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    from job import DEMO_JOB

    r = asyncio.run(report(data, DEMO_JOB))
    print(json.dumps(r, ensure_ascii=False, indent=2))
