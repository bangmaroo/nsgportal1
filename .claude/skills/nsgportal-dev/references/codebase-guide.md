# NSG Portal 코드베이스 가이드

## 프로젝트 개요

**서비스**: NSG 포털(농심 계열사 그룹웨어) 새 게시물 → 카카오톡/디스코드 알림봇
**실행 방식**: Windows Task Scheduler (평일 5분 주기 / 식단은 10시 1회)

## 디렉토리 구조

```
nsgportal1/
├── groupware_notifier/
│   ├── main.py          # 진입점: 상태 관리, 락파일, 게시물 감지 루프
│   ├── scraper.py       # GroupwareScraper: SiteMinder SSO + 스크래핑
│   ├── notifier.py      # KakaoNotifier, DiscordNotifier, MultiNotifier
│   └── setup_token.py   # 카카오 OAuth 초기 토큰 발급 (수동 1회)
├── tests/
│   ├── test_scraper.py
│   ├── test_notifier.py
│   └── test_main.py
└── requirements.txt     # requests, beautifulsoup4, psutil, pytest, responses
```

## 핵심 클래스 및 함수

### GroupwareScraper (scraper.py)

```python
scraper = GroupwareScraper(config, secrets)
scraper.login()               # SiteMinder SSO 로그인
scraper.get_posts(board_id)   # → [{'id': int, 'title': str}, ...]
scraper.get_today_menu()      # → {'date', 'lunch', 'lunch_kcal', 'dinner'} | None
```

**SiteMinder SSO 로그인 플로우** (`login()`):
1. `entry_url` GET → `ssoTarget` hidden 필드 추출
2. PASSWORD = SHA256(password), USER = cmpId.replace('C','') + username
3. `login_url` (login.fcc) POST → 세션 쿠키 발급
4. `sso_login_url` (ssoLogin.jsp) POST → 포털 JSESSIONID 완성

**세션 만료 감지** (`_is_login_page()`):
- 302 redirect + 최종 URL에 `login_url` 포함
- 또는 200 응답에 `<input type="password">` 존재

### 알림 (notifier.py)

```python
notifier = build_notifier(secrets, secrets_path, config)
notifier.send(title, body, url='', header='📬 새 게시물')
```

- `KakaoNotifier`: 401 수신 시 refresh_token으로 자동 갱신 후 재시도
- `DiscordNotifier`: Embed 메시지, 헤더별 색상 분기
- `MultiNotifier`: 개별 실패 로그만 남기고 계속 진행
- `build_notifier()`: `config.notifiers` 배열로 활성화 수단 결정

### State 관리 (main.py)

```python
_write_json_atomic(data, path)  # 원자적 JSON 쓰기 — 반드시 이 함수 사용
```

**state.json 구조**:
```json
{
  "boards": {
    "BB140533555033482": {
      "last_seen_id": 12345,
      "seen_titles": {"12345": "게시물 제목"}
    }
  }
}
```

## config.json 주요 키

```json
{
  "groupware_url": "...",
  "entry_url": "...",
  "login_url": "https://sso.nsgportal.net/.../login.fcc",
  "sso_login_url": "http://www.nsgportal.net/ekp/ssoLogin.jsp",
  "company_id": "CD",
  "ssl_verify": false,
  "dining_url": "...",
  "board_ids": ["BB140533555033482", ...],
  "board_names": {"BB140533555033482": "공지사항"},
  "board_url_overrides": {},
  "post_selector": "tr",
  "post_id_pattern": "fnViewAtcl\\('(\\d+)'",
  "post_title_selector": "a[href*='fnViewAtcl']",
  "notifiers": ["discord"]
}
```

## 게시판 ID 매핑

| ID | 이름 | 이모지 |
|----|------|--------|
| BB140533555033482 | 공지사항 | 📌 |
| BB140304938548009 | 경조사 | 🎊/🎉/🕯️ (키워드 분기) |
| BB140306311362185 | 인사발령 | 👥 |
| BB168050962738658 | NDS 수주정보 | 💼 |
| BB140306307605625 | 교육세미나일정 | 📚 |

## 코딩 패턴

1. **원자적 JSON 쓰기**: `_write_json_atomic()` 항상 사용 (tempfile + os.replace)
2. **로깅**: `logger = logging.getLogger(__name__)`
3. **타입 힌트**: `dict | None` (Python 3.10+ 유니온)
4. **독스트링**: 한국어

## TODOS (미구현 기능)

1. **Heartbeat**: 하루 1회 "✅ 정상 동작 중" 카카오톡 알림
   - `state.json`에 `heartbeat_last_sent` 필드 추가
   - `main.py`의 `run()` 함수에 하루 1회 전송 로직 추가
2. **Rate limit 모니터링**: 폴링 간격 조정 (MVP 2주 운영 후 판단)

## 테스트 패턴

```python
import responses

@responses.activate
def test_something():
    responses.add(responses.GET, 'http://entry.url/',
                  body='<input name="ssoTarget" value="target"/>')
    responses.add(responses.POST, 'https://sso.url/login.fcc',
                  body='<html>redirect to ssoLogin</html>')
    # ...
```

**핵심**: 실제 HTTP 요청 없이 `responses` 라이브러리로 전체 HTTP 레이어 모킹
