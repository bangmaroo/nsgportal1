# TODOs

## Dead man's switch — 스크립트 장애 알림

**What:** 하루에 한 번 "이상 없음" 카카오톡 알림 전송. 스크립트가 실패하면 이 메시지가 안 오므로 자동으로 장애 감지.

**Why:** 현재 스크립트가 실패해도 아무런 알림이 없음. 로그를 직접 보지 않으면 언제부터 동작 중단됐는지 알 수 없음. 알림 앱이 조용히 죽는 것을 방지.

**Pros:** 모니터링 없이도 스크립트 정상 동작 여부 파악 가능. 카카오톡으로 매일 오전 9시 "✅ 그룹웨어 알림 정상 동작 중" 메시지.

**Cons:** notifier.py에 하루 1회 heartbeat 로직 + 마지막 전송 시간 저장 필요. state.json에 heartbeat_last_sent 필드 추가.

**Context:** /plan-eng-review 에서 제안됨. MVP Approach A 검증 완료 후 구현 권장. outside voice에서 발견된 이슈.

**Depends on:** MVP (Approach A) 완성 후

---

## Rate limit 모니터링

**What:** 5분 폴링이 그룹웨어 서버의 세션 인증 rate limit을 유발하는지 모니터링.

**Why:** 구형 그룹웨어는 동일 계정의 반복 요청에 일시 잠금을 걸 수 있음. 특히 세션이 자주 만료되어 재로그인이 잦은 경우 위험.

**Pros:** 문제 발생 시 폴링 간격 조정 (5분 → 10분).

**Cons:** 실제로 문제가 없을 가능성이 높음 (세션 유지 시 재로그인 없음).

**Context:** /plan-eng-review outside voice에서 제안됨.

**Depends on:** MVP 실제 운영 2주 후 확인
