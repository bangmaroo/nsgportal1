---
name: nsgportal-dev
description: NSG Portal 그룹웨어 알림 시스템(nsgportal1) 개발 오케스트레이터. 새 기능 추가, 버그 수정, 리팩토링, 테스트 작성 등 모든 개발 요청을 분석가·엔지니어·테스터 팀으로 조율한다. "nsgportal", "그룹웨어 알림", "카카오", "디스코드 알림", "scraper", "스크래퍼", "heartbeat", "식단", "게시판", "알림봇" 키워드가 포함된 개발 요청 시 반드시 이 스킬을 사용할 것. 다시 실행, 재실행, 업데이트, 수정, 보완 요청에도 사용할 것.
---

# NSG Portal 개발 오케스트레이터

NSG Portal 그룹웨어 알림 시스템의 개발 요청을 분류하고 분석가·엔지니어·테스터 에이전트 팀을 조율한다.

## 실행 모드

**기본**: 에이전트 팀 (analyst → engineer → tester 파이프라인)
**단순 작업** (한 파일 1~5줄 단순 수정, 설정값 변경): 서브 에이전트 직접 처리

## Phase 0: 컨텍스트 확인

`_workspace/` 존재 여부 확인:
- 존재 + 부분 수정 요청 → **부분 재실행** (해당 에이전트만 재호출)
- 존재 + 새 요청 → `_workspace/`를 `_workspace_prev/`로 이동 후 새 실행
- 미존재 → **초기 실행**

## Phase 1: 요청 분류

| 유형 | 처리 |
|------|------|
| 신규 기능 (heartbeat, rate limit 등) | 팀 전체 (Phase 2~5) |
| 버그 수정 (로그인 실패, 파싱 오류 등) | 팀 전체 (Phase 2~5) |
| 리팩토링 | 팀 전체 (Phase 2~5) |
| 단순 수정 (상수값, 텍스트, 설정 추가) | 서브 에이전트 직접 |

코드베이스 상세 정보는 `references/codebase-guide.md` 참조.

## Phase 2: 분석 (analyst)

`analyst` 에이전트에게 원본 요청과 컨텍스트를 전달한다.
analyst가 `_workspace/01_analyst_spec.md` 작성 후 engineer에게 SendMessage.

## Phase 3: 구현 (engineer)

`engineer` 에이전트가 `_workspace/01_analyst_spec.md`를 읽고 구현한다.
구현 완료 후 `_workspace/02_engineer_notes.md` 작성, tester에게 SendMessage.

## Phase 4: 검증 (tester)

`tester` 에이전트가 변경 사항을 검증하고 테스트를 추가한다.
- 테스트 통과 시 오케스트레이터에게 완료 보고
- 테스트 실패 시 engineer에게 SendMessage, 수정 후 재검증 (최대 2회)
- 2회 후 미해결이면 오케스트레이터에게 에스컬레이션

## Phase 5: 결과 종합

1. `_workspace/` 파일들을 읽어 작업 결과 종합
2. 사용자에게 보고:
   - 변경된 파일 목록
   - 주요 변경 내용
   - 테스트 결과
3. 피드백 요청 ("개선할 부분이 있나요?")

## 에러 핸들링

- 에이전트 실패 시 1회 재시도
- 재실패 시 해당 단계 결과 없이 진행하고 사용자에게 보고

## 테스트 시나리오

**정상 흐름**: "heartbeat 기능 추가해줘"
→ analyst 스펙 작성 → engineer 구현 (`main.py`, `state.json` 스키마) → tester 검증 → 사용자 보고

**에러 흐름**: tester 테스트 실패
→ engineer에게 실패 원인 전달 → 수정 후 재검증 → 통과 또는 에스컬레이션
