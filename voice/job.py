#!/usr/bin/env python3
"""Spring 이 넘겨주는 contact_job 한 건.

DB 테이블 대응 (V1__create_initial_schema.sql):
  contact_job        id, elderly_id, intervention_plan_id, attempt_count, idempotency_key
  intervention_plan  elderly_id, risk_snapshot_id, shelter_id, guidance_json, questions_json
  shelter            name, address, open_status
  risk_snapshot      target_start_at, target_end_at, risk_level

원본 전화번호는 DB 에 없다(elderly_profile 은 phone_hash 만 저장). 별도 보관소에서
온다는 전제로 job["to_number"] 에 담아 전달한다. 결과 전송에는 절대 포함하지 않는다.
"""

from __future__ import annotations

import os

# 백엔드 연동 전까지 쓰는 고정값. Spring 이 POST 로 넘겨주는 본문으로 교체된다.
DEMO_JOB: dict = {
    # contact_job
    "contact_job_id": 100,
    "elderly_id": 10,
    "intervention_plan_id": 50,
    "risk_snapshot_id": 7,
    "attempt_count": 1,
    # contact/AGENTS.md: elderlyId + riskSnapshotId + purpose 조합. VARCHAR(160) UK.
    # 우리가 만들지 않는다. Spring 이 만든 값을 그대로 되돌려준다.
    "idempotency_key": "10:7:HEAT_PREVENTION_CALL",
    # 원본 번호 (DB 아님)
    "to_number": os.getenv("TEST_RECIPIENT_PHONE", ""),
    # intervention_plan.guidance_json
    "guidance": {
        "risk_window": "오늘 오후 한 시부터 다섯 시까지",
        "message": "오후 한 시 전부터 시원한 실내에 머물러 주세요.",
        "recommendation": "물을 조금씩 자주 드세요.",
    },
    # intervention_plan.questions_json
    "questions": [
        "쉼터에 가실 의향이 있으신가요?",
        "혼자 이동하실 수 있으신가요?",
        "이동이나 다른 도움이 필요하신가요?",
    ],
    # shelter (intervention_plan.shelter_id 로 조인된 값)
    "shelter": {
        "name": "OO경로당 무더위쉼터",
        "open_status": "OPEN",
        "walk_minutes": 8,
    },
}


REQUIRED = ("contact_job_id", "elderly_id", "intervention_plan_id",
            "attempt_count", "idempotency_key", "guidance", "questions", "shelter")


def check(job: dict) -> dict:
    """Spring 이 보낸 본문 검증. 신뢰 경계라 여기서만은 게으르지 않는다."""
    if missing := [k for k in REQUIRED if k not in job]:
        raise ValueError(f"job 필수 항목 누락: {missing}")
    return job


def load(contact_job_id: int | None = None) -> dict:
    """미연동 상태의 기본 job. 서버 모드에서는 Spring 이 본문으로 직접 넘긴다."""
    return DEMO_JOB


if __name__ == "__main__":
    check(DEMO_JOB)
    try:
        check({"contact_job_id": 1})
    except ValueError as e:
        print(f"OK — {e}")
