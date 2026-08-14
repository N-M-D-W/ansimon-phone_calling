# 안심온 전화 모듈 (ansimon-phone_calling)

폭염 위험이 높을 때 어르신에게 **한국어 AI 예방 안내전화**를 거는 모듈입니다.

이 저장소는 **통화 채널만** 담당합니다. 누구에게 언제 전화할지, 결과로 무슨 업무를 만들지는 **백엔드(Spring)가 결정**합니다.

```
[Spring] 위험 예측 → 행동지침·쉼터 확정 → 담당자 승인
              ↓  승인된 계획 전달
[이 저장소] ClawOps 070 발신 → OpenAI Realtime 한국어 대화 → 답변 구조화
              ↓  결과 콜백
[Spring] 결과 저장 → 지원 업무 생성 / 완료 / 무응답 시 10분 뒤 1회 재전화
```

---

## 1. 필요한 API

전화 1건을 걸려면 **API 두 개**가 필요합니다. 둘 다 없으면 실행되지 않습니다.

### ClawOps — 전화망 (필수)

한국 **070 번호로 실제 전화를 거는** 역할입니다.

- 가입: <https://claw-ops.com> — 개인은 휴대폰 본인인증만 필요 (사업자등록증 불필요)
- 키 발급: <https://platform.claw-ops.com>
- 필요한 값: `CLAWOPS_API_KEY` (`sk_`로 시작), `CLAWOPS_ACCOUNT_ID` (`AC`로 시작)

> **왜 해외 서비스(Twilio, Bland 등)를 쓰지 않나?**
> 2023년부터 한국 통신 3사는 모든 국제 수신전화에 "해외에서 걸려온 전화입니다" 음성 안내와 `국제전화` 화면 표시를 **기본값으로** 붙입니다. 어르신이 우리 AI의 첫 마디를 듣기 전에 이 안내를 먼저 듣게 되어 수신율이 급락합니다. 한국 번호로 발신하는 것이 이 모듈의 핵심 전제입니다.

### OpenAI — 대화 (필수)

통화 중 **한국어로 듣고 말하는** 역할입니다. Realtime API(`gpt-realtime-2`)를 음성-대-음성으로 사용합니다.

- 키 발급: <https://platform.openai.com/api-keys>
- 필요한 값: `OPENAI_API_KEY` (`sk-`로 시작)

> `sk_`(ClawOps, 언더스코어)와 `sk-`(OpenAI, 하이픈)를 혼동하지 마세요.

### 이 모듈이 쓰지 않는 API

기상청·TMAP·무더위쉼터 API는 **백엔드가 호출**합니다. 이 모듈은 백엔드가 확정한 값을 받아 읽어줄 뿐, 날씨나 쉼터를 직접 조회하지 않습니다.

---

## 2. 설치와 실행

```bash
pip install "clawops[agent,openai]"

cp .env.example .env      # 키 입력
python scripts/provision_number.py --create   # 070 번호 발급
python voice/call.py 01012345678              # 발신
```

### 환경변수

| 변수 | 필수 | 설명 |
| --- | --- | --- |
| `CLAWOPS_API_KEY` | ✅ | ClawOps 대시보드에서 발급 |
| `CLAWOPS_ACCOUNT_ID` | ✅ | ClawOps 대시보드에서 발급 |
| `OPENAI_API_KEY` | ✅ | 통화 대화용 |
| `CLAWOPS_FROM_NUMBER` | ⬜ | 발신 070 번호. 비워두면 계정 보유분을 자동으로 사용 |
| `ANSIMON_BACKEND_BASE_URL` | ⬜ | 결과 콜백 대상 (Phase 4에서 사용) |
| `SOCIAL_WORKER_PHONE` | ⬜ | 긴급 시 담당자 연결 |

`.env`는 **절대 커밋하지 않습니다.** `.gitignore`에 등록돼 있습니다.

---

## 3. 백엔드 연동

### 3-1. 책임 경계

가장 중요한 원칙입니다. **이 모듈은 판단하지 않습니다.**

| Spring이 하는 일 | 이 모듈이 하는 일 |
| --- | --- |
| 동의·연락 허용시간 확인 | 승인된 계획을 읽어 한 명에게 한 번 발신 |
| 위험도 계산, 쉼터 선정 | 승인된 사실만 말하기 |
| `contactJob` 생성, 멱등키 검사 | 질문 하나씩, 답변 구조화 |
| 결과 저장, 지원 업무 생성 | 결과를 Spring에 전달 |
| **무응답 시 10분 뒤 재전화 예약** | 긴급 증상 시 플래그만 올림 |

이 모듈이 **하면 안 되는 것**: 누구에게 전화할지 결정, 위험 등급·쉼터 재계산, 재시도 자체 스케줄링, 자유문장만 반환하기.

> ClawOps SDK 내부의 HTTP 재시도와 "10분 뒤 업무 재전화"는 **완전히 다른 개념**입니다. 업무 재전화는 반드시 Spring 상태 머신만 예약합니다.

### 3-2. Spring → 전화 모듈 (입력)

승인된 `interventionPlan`을 넘깁니다.

```json
{
  "contactJobId": 991,
  "attemptNo": 1,
  "toNumber": "01012345678",
  "riskWindow": "오늘 오후 1시부터 5시까지",
  "guidance": [
    "오후 1시 전부터 시원한 실내에 머물러 주세요.",
    "물을 조금씩 자주 드세요."
  ],
  "shelter": {
    "name": "OO경로당 무더위쉼터",
    "travelMinutes": 8,
    "openStatus": "OPEN"
  },
  "questions": [
    "쉼터에 가실 의향이 있으신가요?",
    "혼자 이동하실 수 있으신가요?",
    "이동이나 다른 도움이 필요하신가요?"
  ],
  "planVersion": 1
}
```

**입력 검증에 실패하면 발신하지 않습니다.** `contactJobId`·`attemptNo`·`toNumber`가 있어야 하고, `guidance`와 `questions`가 비면 안 되며, 쉼터가 닫혔거나 데이터가 오래됐으면 발신을 중단합니다.

현재 `voice/call.py`에서는 이 값이 상단 `PLAN` 딕셔너리에 데모용으로 고정돼 있습니다. Phase 4에서 이 자리를 Spring 응답으로 바꾸면 됩니다.

### 3-3. 전화 모듈 → Spring (결과)

```json
{
  "contactJobId": 991,
  "contactStatus": "ANSWERED",
  "shelterIntent": "YES",
  "canMoveAlone": "NO",
  "helpNeeded": "YES",
  "symptomMentioned": "NO",
  "summary": "쉼터 이동 의향이 있으나 혼자 이동하기 어렵다고 답변함.",
  "confidence": 0.93,
  "endedAt": "2026-08-14T15:20:00+09:00"
}
```

허용 enum:

| 필드 | 값 |
| --- | --- |
| `contactStatus` | `ANSWERED` · `NO_ANSWER` · `FAILED` |
| `shelterIntent` | `YES` · `NO` · `UNKNOWN` |
| `canMoveAlone` | `YES` · `NO` · `UNKNOWN` |
| `helpNeeded` | `YES` · `NO` · `UNKNOWN` |
| `symptomMentioned` | `YES` · `NO` · `UNKNOWN` |

**답을 듣지 못한 필드는 추측하지 않고 `UNKNOWN`으로 둡니다.** 이게 이 모듈의 안전 원칙입니다.

운영 메타데이터는 본문과 분리해 저장합니다: `provider=CLAWOPS`, `providerCallId`, `attemptNo`, `planVersion`, `receivedAt`.

### 3-4. 결과 → 업무 매핑 (Spring이 수행)

| 결과 | 생성되는 업무 |
| --- | --- |
| `symptomMentioned=YES` | **CRITICAL** 즉시 사람 확인 |
| `helpNeeded=YES` | HIGH 지원 업무 |
| `shelterIntent=YES` + `canMoveAlone=NO` | HIGH 쉼터 이동 지원 |
| 일부 필드 `UNKNOWN` | MEDIUM 수동 확인 |
| 그 외 | COMPLETED |
| 1차 `NO_ANSWER` | 종료 시각 + 10분에 2차 시도 예약 |
| 2차 `NO_ANSWER` | UNCONFIRMED → HIGH 수동 확인 |
| `FAILED` | 운영자 확인 업무 |

### 3-5. 멱등성

권장 멱등키: `contactJobId + attemptNo + eventType`

같은 콜백이 여러 번 들어와도 `call_observation`과 지원 업무는 **한 번만** 생성되어야 합니다.

---

## 4. 파일 구조

| 파일 | 역할 |
| --- | --- |
| `voice/call.py` | **발신 실행.** 번호를 받아 한국어 예방 안내전화 1건 |
| `scripts/provision_number.py` | ClawOps 키 점검 + 070 번호 조회·발급 |
| `.env.example` | 환경변수 템플릿 |

---

## 5. 현재 범위와 다음 단계

**구현됨** — 발신, 한국어 대화, 승인된 사실 안내, 질문 순차 진행, 통화번호 로그 마스킹

**미구현** — 결과 enum 구조화(`@agent.tool`), Spring 결과 콜백, 입력 계약 validator, 무응답 재시도 연동

> 우선순위는 **팀원 휴대전화로 실발신 1건 성공**입니다. 첫 실발신이 성공하기 전에는 대시보드나 고급 기능을 넓히지 않습니다.

---

## 6. 안전 원칙

- 데모는 **가상 대상자와 팀원 본인 번호**로만 진행합니다.
- 첫 문장에서 AI임과 연락 목적을 밝힙니다. (AI 기본법 2026-01-22 시행, 고지 의무)
- 통화 녹음은 비활성화(`recording=False`)합니다.
- 로그에 전화번호는 **뒤 4자리만** 남깁니다.
- AI는 의료 진단을 하지 않고 119에 자동 신고하지 않습니다. 사람 확인을 요청합니다.
- 발신번호는 본인 명의로 사전등록된 번호만 사용합니다. (전기통신사업법 제84조의2)
