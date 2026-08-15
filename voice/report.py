#!/usr/bin/env python3
"""통화 전사(STT) -> 구조화 결과 -> Spring 전송.

계약은 CLAWOPS_PHONE_WORKFLOW.txt §2.2 / §2.3 을 그대로 따른다.
LLM 교체(OpenAI -> Alan)는 summarize() 한 곳만 고치면 된다.

단독 실행으로 이미 저장된 전사를 다시 정리할 수 있다:
    python voice/report.py transcript_20260814_1530.json
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

# 어르신 답변에서 뽑아낼 항목. 값은 반드시 YES/NO/UNKNOWN.
FIELDS = {
    "shelterIntent": "무더위쉼터에 갈 의향이 있다고 했는가",
    "canMoveAlone": "쉼터까지 혼자 이동할 수 있다고 했는가",
    "helpNeeded": "이동이나 다른 도움이 필요하다고 했는가",
    "symptomMentioned": "어지럼증·두통·구역질·의식저하 등 온열질환 의심 증상을 언급했는가",
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
{{"shelterIntent":"...","canMoveAlone":"...","helpNeeded":"...",
  "symptomMentioned":"...","summary":"...","confidence":0.0}}"""


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
    out["summary"] = str(raw.get("summary") or "")[:500]
    try:
        out["confidence"] = round(min(max(float(raw.get("confidence", 0)), 0.0), 1.0), 2)
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    return out


def build_result(contact_job_id: int, attempt_no: int,
                 analysis: dict | None, status: str) -> dict:
    """§2.2 결과 본문. 원본 전사는 싣지 않고 transcriptRef 만 넣는다."""
    base = {k: "UNKNOWN" for k in FIELDS} | {"summary": "", "confidence": 0.0}
    return {
        "contactJobId": contact_job_id,
        "contactStatus": status,
        **(analysis or base),
        "transcriptRef": f"restricted://call/{contact_job_id}/attempt/{attempt_no}",
        "endedAt": datetime.now(KST).isoformat(timespec="seconds"),
    }


async def analyze(turns: list[dict], contact_job_id: int, attempt_no: int) -> dict:
    """전사 -> 결과 본문. 어르신 발화가 없으면 LLM 을 부르지 않는다."""
    if not any(t["role"] == "user" for t in turns):
        return build_result(contact_job_id, attempt_no, None, "NO_ANSWER")
    dialogue = "\n".join(
        f"{'어르신' if t['role'] == 'user' else 'AI'}: {t['text']}" for t in turns
    )
    try:
        analysis = coerce(await summarize(dialogue))
    except Exception as e:  # LLM 실패로 통화 결과를 잃지 않는다
        print(f"  ! 요약 실패 ({type(e).__name__}) — UNKNOWN 으로 전송", file=sys.stderr)
        return build_result(contact_job_id, attempt_no, None, "ANSWERED")
    return build_result(contact_job_id, attempt_no, analysis, "ANSWERED")


async def send(result: dict, meta: dict) -> None:
    """Spring 전송. §2.3 멱등키를 헤더로 넘긴다.

    ponytail: 엔드포인트 경로와 인증 방식은 Phase 0 미확정(문서 §5).
    백엔드와 합의되면 아래 path/헤더만 고치면 된다.
    """
    base = os.getenv("ANSIMON_BACKEND_BASE_URL")
    body = {"result": result, "meta": meta}
    if not base:
        print("\n=== Spring 전송 예정 payload (BASE_URL 미설정) ===")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return

    import aiohttp

    url = f"{base.rstrip('/')}/api/contact-jobs/{result['contactJobId']}/outcome"
    headers = {"Idempotency-Key": f"{result['contactJobId']}:{meta['attemptNo']}:call_result"}
    if tok := os.getenv("ANSIMON_BACKEND_TOKEN"):
        headers["Authorization"] = f"Bearer {tok}"

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=10) as r:
            print(f"Spring 전송 {r.status} {url}")


async def report(turns: list[dict], contact_job_id: int, attempt_no: int,
                 provider_call_id: str | None = None) -> dict:
    """call.py 에서 부르는 진입점."""
    result = await analyze(turns, contact_job_id, attempt_no)
    meta = {
        "provider": "CLAWOPS",
        "providerCallId": provider_call_id,
        "attemptNo": attempt_no,
        "planVersion": 1,  # Spring 연동 시 인자로 승격
        "receivedAt": datetime.now(KST).isoformat(timespec="seconds"),
    }
    await send(result, meta)
    return result


if __name__ == "__main__":
    # 저장된 전사를 다시 정리 (LLM 프롬프트 튜닝용)
    assert coerce({"shelterIntent": "maybe"})["shelterIntent"] == "UNKNOWN"
    assert coerce({"confidence": 5})["confidence"] == 1.0
    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) != 2:
        sys.exit("사용법: python voice/report.py transcript_YYYYMMDD_HHMMSS.json")
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(asyncio.run(report(data, 991, 1)), ensure_ascii=False, indent=2))
