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
import logging
from bs4 import BeautifulSoup
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
        self._logged_in = False

    def login(self) -> None:
        """그룹웨어에 로그인하고 세션 쿠키를 획득한다."""
        login_url = self.config.get('login_url') or self.config['groupware_url'] + '/login'

        # 로그인 페이지 GET (CSRF 토큰이 있으면 추출)
        resp = self.session.get(login_url, timeout=30)
        resp.raise_for_status()

        payload = {
            'username': self.secrets['groupware_username'],
            'password': self.secrets['groupware_password'],
        }

        soup = BeautifulSoup(resp.text, 'html.parser')
        for name in _COMMON_CSRF_NAMES:
            field = soup.find('input', {'name': name})
            if field:
                payload[name] = field.get('value', '')
                logger.debug('CSRF token found: %s', name)
                break

        resp = self.session.post(login_url, data=payload, timeout=30, allow_redirects=True)

        if self._login_failed(resp):
            raise RuntimeError(
                'Login failed — credentials rejected or CSRF token mismatch. '
                'Check groupware_username/groupware_password in secrets.json.'
            )

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
        """POST 로그인 응답에서 실패 여부를 판별한다 (URL 체크 제외)."""
        soup = BeautifulSoup(resp.text, 'html.parser')
        return bool(soup.find('input', {'type': 'password'}))

    def _parse_posts(self, html: str, board_id: str) -> list[dict]:
        """HTML에서 게시물 목록을 파싱한다."""
        soup = BeautifulSoup(html, 'html.parser')

        selector = self.config.get('post_selector', 'tr[data-id]')
        id_attr = self.config.get('post_id_attr', 'data-id')
        title_selector = self.config.get('post_title_selector', 'td.title a')

        rows = soup.select(selector)
        if not rows:
            logger.warning(
                'post_selector %r returned 0 results for board %r. '
                'Update post_selector in config.json if the groupware layout changed.',
                selector, board_id
            )
            return []

        posts = []
        for row in rows:
            raw_id = row.get(id_attr, '').strip()
            if not raw_id or not raw_id.isdigit():
                continue

            title_el = row.select_one(title_selector)
            title = title_el.get_text(strip=True) if title_el else f'Post #{raw_id}'

            posts.append({'id': int(raw_id), 'title': title})

        return posts
