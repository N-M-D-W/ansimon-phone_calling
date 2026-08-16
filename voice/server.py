#!/usr/bin/env python3
"""Spring 이 호출하는 발신 엔드포인트.

백엔드 .env.example 의 PHONE_API_BASE_URL=http://localhost:9000 이 여기를 가리킨다.
경로는 백엔드 AGENTS.md 규약대로 내부용 /internal/v1/**.

    python voice/server.py

    POST /internal/v1/calls   body = contact_job 한 건 (voice/job.py 모양)
         → 202 즉시 반환. 통화는 백그라운드, 결과는 report.py 가 Spring 으로 콜백.
    GET  /health
"""

from __future__ import annotations

import asyncio
import os

from aiohttp import web

from call import facts, main, setup
from job import check
from report import report

# 같은 job 이 두 번 들어와 어르신께 전화가 두 번 가는 것을 막는다.
# ponytail: 프로세스 메모리. 서버를 여러 대로 늘리면 Redis 나 DB 락으로 올려야 한다.
INFLIGHT: set[int] = set()


def ok(data: dict, status: int = 200) -> web.Response:
    """백엔드 공통 응답 포맷 {success, data, error}."""
    return web.json_response({"success": True, "data": data, "error": None}, status=status)


async def run(job: dict, to: str) -> None:
    try:
        await main(job, to)
    except Exception as e:                     # 통화 실패를 Spring 이 모르면 안 된다
        print(f"! 발신 실패 {type(e).__name__}: {e}", flush=True)
        await report([], job, end_reason="error")
    finally:
        INFLIGHT.discard(job["contact_job_id"])


async def calls(request: web.Request) -> web.Response:
    try:
        job = check(await request.json())
        facts(job)          # 안내할 내용이 없으면 전화를 걸기 전에 되돌린다
    except (ValueError, TypeError, AttributeError) as e:
        raise web.HTTPBadRequest(reason=str(e))

    to = job.get("to_number")                  # 원본 번호는 DB 아닌 별도 보관소 값
    if not to:
        raise web.HTTPBadRequest(reason="to_number 없음")

    jid = job["contact_job_id"]
    if jid in INFLIGHT:
        return ok({"contact_job_id": jid, "accepted": False, "reason": "already_calling"})
    INFLIGHT.add(jid)

    asyncio.create_task(run(job, to))          # 통화는 60~90초. 응답을 붙잡아두지 않는다
    return ok({"contact_job_id": jid, "accepted": True}, status=202)


def app() -> web.Application:
    a = web.Application()
    a.add_routes([
        web.post("/internal/v1/calls", calls),
        web.get("/health", lambda _: ok({"status": "UP"})),
    ])
    return a


if __name__ == "__main__":
    setup()
    web.run_app(app(), port=int(os.getenv("PHONE_API_PORT", "9000")))
