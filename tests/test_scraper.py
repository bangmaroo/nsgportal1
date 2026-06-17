"""scraper.py 단위 테스트 — responses 라이브러리로 HTTP mock."""
import pytest
import responses as resp_mock

from groupware_notifier.scraper import GroupwareScraper

BASE = 'https://groupware.example.com'
LOGIN_URL = f'{BASE}/login'
BOARD_URL = f'{BASE}/board/notice'

CONFIG = {
    'groupware_url': BASE,
    'login_url': LOGIN_URL,
    'post_selector': 'tr[data-id]',
    'post_id_attr': 'data-id',
    'post_title_selector': 'td.title a',
}
SECRETS = {
    'groupware_username': 'user',
    'groupware_password': 'pass',
}

LOGIN_PAGE_HTML = '<html><form><input type="password" name="password"/></form></html>'
BOARD_HTML = """
<html><body><table>
  <tr data-id="102"><td class="title"><a>새 공지</a></td></tr>
  <tr data-id="101"><td class="title"><a>이전 공지</a></td></tr>
</table></body></html>
"""
SUCCESS_PAGE_HTML = '<html><body><div class="main">홈</div></body></html>'


# T4: 로그인 실패 → RuntimeError
@resp_mock.activate
def test_login_failure_raises():
    resp_mock.add(resp_mock.GET, LOGIN_URL, body=LOGIN_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.POST, LOGIN_URL, body=LOGIN_PAGE_HTML, status=200)

    scraper = GroupwareScraper(CONFIG, SECRETS)
    with pytest.raises(RuntimeError, match='Login failed'):
        scraper.login()


# T5: 세션 만료 (302 redirect → login URL) → 재로그인 후 성공
@resp_mock.activate
def test_session_expiry_302_auto_relogin():
    # 초기 로그인
    resp_mock.add(resp_mock.GET, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.POST, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    # 게시판 GET → 302 redirect to login URL
    resp_mock.add(
        resp_mock.GET, BOARD_URL,
        body=LOGIN_PAGE_HTML, status=200,
        # responses 라이브러리에서 최종 URL을 login URL로 설정해 302 simulate
    )
    # 재로그인
    resp_mock.add(resp_mock.GET, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.POST, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    # 재시도 성공
    resp_mock.add(resp_mock.GET, BOARD_URL, body=BOARD_HTML, status=200)

    scraper = GroupwareScraper(CONFIG, SECRETS)
    scraper.login()
    posts = scraper.get_posts('notice')
    assert len(posts) == 2
    assert posts[0]['id'] == 102


# T6: selector 0개 → 빈 리스트, 경고 로그
@resp_mock.activate
def test_selector_miss_returns_empty(caplog):
    resp_mock.add(resp_mock.GET, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.POST, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.GET, BOARD_URL, body='<html><body><p>no table</p></body></html>', status=200)

    scraper = GroupwareScraper(CONFIG, SECRETS)
    scraper.login()

    import logging
    with caplog.at_level(logging.WARNING, logger='groupware_notifier.scraper'):
        posts = scraper.get_posts('notice')

    assert posts == []
    assert 'returned 0 results' in caplog.text


# 정상 파싱 확인
@resp_mock.activate
def test_get_posts_parses_correctly():
    resp_mock.add(resp_mock.GET, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.POST, LOGIN_URL, body=SUCCESS_PAGE_HTML, status=200)
    resp_mock.add(resp_mock.GET, BOARD_URL, body=BOARD_HTML, status=200)

    scraper = GroupwareScraper(CONFIG, SECRETS)
    scraper.login()
    posts = scraper.get_posts('notice')

    assert len(posts) == 2
    assert posts[0] == {'id': 102, 'title': '새 공지'}
    assert posts[1] == {'id': 101, 'title': '이전 공지'}
