"""
그룹웨어 로그인 및 게시물 목록 조회.

로그인 흐름:
  GET 로그인 페이지 (CSRF 토큰 추출)
  → POST 로그인
  → GET 게시판 페이지
  → HTML 파싱 → 게시물 목록 반환

세션 만료 감지:
  GET 게시판 시 302 redirect → login_url 포함 여부 확인
  또는 200이지만 응답 HTML에 password 입력 필드 존재 확인
  → 재로그인 후 재시도 (1회)
"""
import hashlib
import logging
import re
import urllib3
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)

_COMMON_CSRF_NAMES = ['_csrf', 'csrf_token', 'csrfmiddlewaretoken', '_token']


class GroupwareScraper:
    def __init__(self, config: dict, secrets: dict):
        self.config = config
        self.secrets = secrets
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        })
        # 회사 내부 CA 인증서를 신뢰하지 못하는 환경에서는 config에 "ssl_verify": false 설정
        ssl_verify = config.get('ssl_verify', True)
        self.session.verify = ssl_verify
        if not ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._logged_in = False

    def login(self) -> None:
        """그룹웨어 SiteMinder SSO 로그인.

        플로우 (브라우저 JS fnSSOCommonLogin 동일):
          1) 포털 진입 페이지 GET → ssoTarget hidden 필드 추출
          2) PASSWORD = SHA256(password)
          3) USER = cmpId에서 'C' 제거한 prefix + username  (CD→D, D+0815138=D0815138)
          4) SiteMinder login.fcc에 직접 POST → 세션 쿠키 발급
        """
        entry_url = self.config.get('entry_url') or self.config['groupware_url']
        entry_resp = self.session.get(entry_url, timeout=30, allow_redirects=True)

        # ssoTarget 필드에서 SiteMinder TARGET 값 구성
        soup = BeautifulSoup(entry_resp.text, 'html.parser')
        sso_target_field = soup.find('input', {'name': 'ssoTarget'})
        sso_target_raw = sso_target_field.get('value', '') if sso_target_field else ''

        login_url = self.config['login_url']
        sso_domain = urlparse(login_url).hostname  # sso.nsgportal.net
        sso_target = (
            f'HTTP://{sso_domain}:8000/redirect.jsp?returl='
            + sso_target_raw.replace('-SM-', '')
        )

        # JS 로직: USER = cmpId.replace('C','') + username  (예: 'CD'→'D', 'D'+'0815138'='D0815138')
        cmp_id = self.config.get('company_id', 'CD')
        cmp_prefix = cmp_id.replace('C', '', 1)
        user_id = cmp_prefix + self.secrets['groupware_username']

        # 비밀번호 SHA256 해싱 (JS: password = SHA256(password))
        pw_hash = hashlib.sha256(
            self.secrets['groupware_password'].encode('utf-8')
        ).hexdigest()

        payload = {
            'USER': user_id,
            'PASSWORD': pw_hash,
            'GUID': '0',
            'SMAUTHREASON': '0',
            'BUFFER': 'endl',
            'TARGET': sso_target,
        }

        resp = self.session.post(login_url, data=payload, timeout=30, allow_redirects=True)

        if self._login_failed(resp):
            raise RuntimeError(
                'Login failed — credentials rejected or CSRF token mismatch. '
                'Check groupware_username/groupware_password in secrets.json.'
            )

        # SiteMinder 인증 후 포털 세션 완성:
        # redirectlogin_gw.jsp가 JS로 ssoLogin.jsp에 POST → 포털 JSESSIONID 발급
        soup = BeautifulSoup(resp.text, 'html.parser')
        sso_login_url = self.config.get('sso_login_url', 'http://www.nsgportal.net/ekp/ssoLogin.jsp')
        self.session.post(sso_login_url, data={}, timeout=30, allow_redirects=True)

        self._logged_in = True
        logger.info('Groupware login successful')

    def get_posts(self, board_id: str) -> list[dict]:
        """
        지정한 게시판의 게시물 목록을 반환한다.

        반환값: [{'id': int, 'title': str}, ...]
        세션 만료 시 자동 재로그인 후 재시도.
        selector 결과 0개 + HTTP 200이면 경고 로그만 남기고 빈 리스트 반환.
        """
        if not self._logged_in:
            self.login()

        board_url = self._board_url(board_id)
        resp = self._get_with_session_check(board_url)
        return self._parse_posts(resp.text, board_id)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _board_url(self, board_id: str) -> str:
        overrides = self.config.get('board_url_overrides', {})
        if board_id in overrides:
            return overrides[board_id]
        template = self.config.get('board_url_template')
        if template:
            return template.format(board_id=board_id)
        base = self.config['groupware_url'].rstrip('/')
        return f'{base}/board/{board_id}'

    def _get_with_session_check(self, url: str) -> requests.Response:
        """GET 요청 후 세션 만료를 감지하면 재로그인 후 재시도한다 (1회)."""
        resp = self.session.get(url, timeout=30, allow_redirects=True)

        if self._is_login_page(resp):
            logger.warning('Session expired — re-logging in...')
            self._logged_in = False
            self.login()
            resp = self.session.get(url, timeout=30, allow_redirects=True)

            if self._is_login_page(resp):
                raise RuntimeError('Re-login succeeded but still getting login page. Check config.')

        resp.raise_for_status()
        return resp

    def _is_login_page(self, resp: requests.Response) -> bool:
        """
        GET 게시판 응답에서 세션 만료 여부를 판별한다.

        두 경우를 모두 커버:
          (1) 302 redirect → 최종 URL에 login_url 문자열 포함
          (2) 200 응답이지만 <input type="password"> 존재 (200 + 로그인 페이지 반환)
        """
        login_url = self.config.get('login_url', '')

        if login_url and login_url in resp.url:
            return True

        soup = BeautifulSoup(resp.text, 'html.parser')
        return bool(soup.find('input', {'type': 'password'}))

    def _login_failed(self, resp: requests.Response) -> bool:
        """POST 로그인 응답에서 실패 여부를 판별한다."""
        if 'SMERROR' in resp.url or 'ssoErrMsg' in resp.url:
            logger.error('SSO 로그인 에러: %s', resp.url)
            return True
        # SiteMinder가 다시 로그인 폼으로 돌아온 경우
        if 'login.fcc' in resp.url:
            logger.error('SiteMinder 로그인 실패, 최종 URL: %s', resp.url)
            return True
        return False

    def _parse_posts(self, html: str, board_id: str) -> list[dict]:
        """HTML에서 게시물 목록을 파싱한다.

        post_id_pattern이 설정된 경우: 링크 href에서 정규식으로 ID 추출
        post_id_attr이 설정된 경우: tr의 HTML 속성에서 직접 추출
        """
        soup = BeautifulSoup(html, 'html.parser')

        selector = self.config.get('post_selector', 'tr')
        id_pattern = self.config.get('post_id_pattern')
        id_attr = self.config.get('post_id_attr')
        title_selector = self.config.get('post_title_selector', 'a[href*="fnViewAtcl"]')

        rows = soup.select(selector)
        if not rows:
            logger.warning(
                'post_selector %r returned 0 results for board %r. '
                'Update post_selector in config.json if the groupware layout changed.',
                selector, board_id
            )
            return []

        compiled = re.compile(id_pattern) if id_pattern else None
        posts = []
        seen_ids = set()

        for row in rows:
            raw_id = None

            if compiled:
                # 링크 href에서 정규식으로 ID 추출
                for a in row.find_all('a', href=True):
                    m = compiled.search(a['href'])
                    if m:
                        raw_id = m.group(1)
                        break
            elif id_attr:
                raw_id = row.get(id_attr, '').strip() or None

            if not raw_id or not raw_id.isdigit():
                continue

            post_id = int(raw_id)
            if post_id in seen_ids:
                continue
            seen_ids.add(post_id)

            title_el = row.select_one(title_selector)
            title = title_el.get_text(strip=True) if title_el else f'Post #{post_id}'

            posts.append({'id': post_id, 'title': title})

        return posts

    def get_today_menu(self) -> dict | None:
        """금일 식단을 반환한다.

        반환값: {'date': str, 'lunch': str, 'lunch_kcal': str, 'dinner': str} 또는 None
        """
        from datetime import date as date_

        dining_url = self.config.get('dining_url')
        if not dining_url:
            logger.warning('config에 dining_url이 없습니다.')
            return None

        if not self._logged_in:
            self.login()

        resp = self.session.get(dining_url, timeout=30, allow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, 'html.parser')
        tables = soup.find_all('table')
        if len(tables) < 2:
            logger.warning('식단 테이블 구조를 찾을 수 없습니다.')
            return None

        # 날짜 헤더에서 오늘 열 인덱스 찾기
        date_cells = tables[0].find_all('tr')[0].find_all(['th', 'td'])
        today_str = date_.today().strftime('%Y.%m.%d')
        col_idx = next(
            (i for i, td in enumerate(date_cells) if td.get_text(strip=True) == today_str),
            None,
        )
        if col_idx is None:
            logger.warning('오늘(%s) 식단이 주간 테이블에 없습니다.', today_str)
            return None

        menu_rows = tables[1].find_all('tr')

        def _cell(row_idx: int) -> str:
            if row_idx >= len(menu_rows):
                return ''
            cells = menu_rows[row_idx].find_all(['th', 'td'])
            if col_idx >= len(cells):
                return ''
            return cells[col_idx].get_text(separator='\n', strip=True)

        # 칼로리 문자열 정리: '930\nkcal/단백질:\n31\ng' → '930kcal / 단백질 31g'
        raw_kcal = re.sub(r'\s+', '', _cell(2))                    # 공백 제거
        kcal = re.sub(r'kcal/단백질:(\d+)g', r'kcal / 단백질 \1g', raw_kcal)

        return {
            'date': today_str,
            'lunch': _cell(1),
            'lunch_kcal': kcal,
            'dinner': _cell(4),
        }
