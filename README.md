# 그룹웨어 알림 (카카오톡 + 디스코드)

NSG 포털 그룹웨어의 새 게시물과 식단 정보를 카카오톡·디스코드로 알려주는 자동화 스크립트.

## 기능

- **새 게시물 알림**: 지정한 게시판에 새 글이 올라오면 카카오톡·디스코드로 즉시 알림
- **금일 식단 알림**: 평일 오전 10시에 당일 중식/석식 메뉴를 카카오톡·디스코드로 전송
- **중복 실행 방지**: PID 기반 lockfile로 동시에 여러 프로세스가 뜨지 않도록 처리
- **토큰 자동 갱신**: 카카오 access token 만료 시 refresh token으로 자동 재발급
- **알림 수단 선택 가능**: 카카오톡·디스코드 각각 독립 설정 (둘 다, 하나만, 또는 둘 다 비활성화 가능)

## 모니터링 게시판

| 게시판 | 게시판 ID |
|--------|-----------|
| 공지사항 | BB140533555033482 |
| 경조사 | BB140304938548009 |
| 인사발령 | BB140306311362185 |
| NDS 수주정보 | BB168050962738658 |
| 교육세미나일정 | BB140306307605625 |

## 설치

### 1. 의존 패키지 설치

```bash
pip install requests beautifulsoup4 psutil
```

### 2. config.json 설정

`groupware_notifier/config.json` 파일을 생성한다 (이미 저장소에 포함).  
회사 코드(`company_id`) 등 환경에 맞게 수정한다.

```json
{
  "groupware_url": "http://www.nsgportal.net/",
  "entry_url": "http://www.nsgportal.net/ekp/ssoLogin.jsp",
  "login_url": "https://sso.nsgportal.net:8888/siteminderagent/forms/login.fcc",
  "company_id": "CD",
  "ssl_verify": false,
  "dining_url": "...",
  "board_ids": ["BB140533555033482", ...],
  "board_names": {"BB140533555033482": "공지사항", ...}
}
```

### 3. secrets.json 생성

`groupware_notifier/secrets.json`을 직접 생성하고 아래 항목을 입력한다.  
(git에서 제외된 파일)

```json
{
  "groupware_username": "사번 (예: 0815138)",
  "groupware_password": "그룹웨어 비밀번호",
  "kakao_rest_api_key": "",
  "kakao_client_secret": "",
  "access_token": "",
  "refresh_token": "",
  "expires_at": 0,
  "discord_webhook_url": ""
}
```

카카오톡만 사용하려면 `discord_webhook_url`을 비워두거나 삭제한다.  
디스코드만 사용하려면 카카오톡 항목(`access_token` 등)을 비워두면 된다.

### 4. 디스코드 웹훅 설정 (선택)

1. 디스코드 서버 → 채널 설정 → **연동** → **웹후크 만들기**
2. 웹훅 URL을 복사해 `secrets.json`의 `discord_webhook_url`에 붙여넣는다.

```json
{
  "discord_webhook_url": "https://discord.com/api/webhooks/..."
}
```

디스코드만 사용하려면 카카오톡 항목은 비워두면 된다.

### 5. 카카오 토큰 발급

#### 카카오 개발자 콘솔 설정 (최초 1회)

1. [https://developers.kakao.com](https://developers.kakao.com) 에서 앱 생성
2. **플랫폼** → Web → 사이트 도메인: `https://example.com` 추가
3. **카카오 로그인** → 활성화 ON
4. **카카오 로그인** → Redirect URI: `https://example.com/oauth` 추가
5. **동의항목** → 카카오톡 메시지 전송(`talk_message`) 활성화
6. **보안** → Client Secret 사용 안함 (또는 값을 `kakao_client_secret`에 입력)

#### 토큰 발급

```bash
python groupware_notifier/setup_token.py
```

브라우저에서 카카오 로그인 후 리다이렉트된 URL을 복사해 붙여넣으면  
`access_token`과 `refresh_token`이 `secrets.json`에 자동 저장된다.

## 실행

### 새 게시물 알림 (수동 실행)

```bash
python groupware_notifier/main.py
```

- 첫 실행 시: 현재 게시물을 기준점으로 저장하고 알림 없이 종료
- 이후 실행 시: 기준점 이후의 새 글만 카카오톡으로 전송

### 금일 식단 알림

```bash
python groupware_notifier/main.py --meal
```

## Windows Task Scheduler 등록

### 새 게시물 알림 (5분 주기)

| 항목 | 값 |
|------|-----|
| 프로그램 | `C:\...\python.exe` |
| 인수 | `groupware_notifier\main.py` |
| 시작 위치 | `C:\Users\jkkim\workspace\nsgportal1` |
| 트리거 | 매일 08:00 시작, **5분마다** 반복, 12시간 동안 |
| 조건 | 네트워크 연결 시에만 실행 |

### 식단 알림 (평일 오전 10시)

| 항목 | 값 |
|------|-----|
| 프로그램 | `C:\...\python.exe` |
| 인수 | `groupware_notifier\main.py --meal` |
| 시작 위치 | `C:\Users\jkkim\workspace\nsgportal1` |
| 트리거 | 매주 월~금, 10:00 |

> `python.exe` 경로 확인: `python -c "import sys; print(sys.executable)"`

## 파일 구조

```
groupware_notifier/
├── main.py          # 진입점 (새 글 알림 / --meal 식단 알림)
├── scraper.py       # 그룹웨어 로그인 및 게시물·식단 스크래핑
├── notifier.py      # 카카오톡 메시지 전송 및 토큰 관리
├── setup_token.py   # 카카오 OAuth 토큰 최초 발급 (1회 실행)
├── config.json      # 게시판·URL 등 설정 (저장소 포함)
├── secrets.json     # 계정·토큰 정보 (git 제외)
└── state.json       # 마지막 확인 게시물 ID 저장 (git 제외)
logs/
└── run.log          # 실행 로그 (최대 5MB × 2개 로테이션)
```

## 게시판 추가 방법

`config.json`의 `board_ids`와 `board_names`에 항목을 추가한다.  
URL 형식이 기본 템플릿과 다른 경우 `board_url_overrides`에 전체 URL을 지정한다.

```json
{
  "board_ids": ["...", "새_게시판_ID"],
  "board_names": {"새_게시판_ID": "게시판 이름"},
  "board_url_overrides": {
    "새_게시판_ID": "http://www.nsgportal.net/ekp/board/atcl.do?..."
  }
}
```

새 게시판을 추가하면 첫 실행 시 기존 게시물이 모두 발송된다.  
이후 실행부터는 새 글만 알림이 온다.
