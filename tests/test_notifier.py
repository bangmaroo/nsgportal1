"""notifier.py 단위 테스트 — responses 라이브러리로 Kakao API mock."""
import json
import time
from pathlib import Path

import pytest
import responses as resp_mock

from groupware_notifier.notifier import KakaoNotifier

KAKAO_MSG_URL = 'https://kapi.kakao.com/v2/api/talk/memo/default/send'
KAKAO_TOKEN_URL = 'https://kauth.kakao.com/oauth/token'

BASE_SECRETS = {
    'kakao_rest_api_key': 'test_rest_key',
    'kakao_client_secret': '',
    'access_token': 'old_access',
    'refresh_token': 'old_refresh',
    'expires_at': int(time.time()) + 3600,
}


def make_notifier(tmp_path: Path, secrets: dict | None = None) -> KakaoNotifier:
    s = {**BASE_SECRETS, **(secrets or {})}
    secrets_path = tmp_path / 'secrets.json'
    secrets_path.write_text(json.dumps(s), encoding='utf-8')
    return KakaoNotifier(s, secrets_path)


# T7: Kakao 401 → 토큰 갱신 성공 → 알림 전송 성공
@resp_mock.activate
def test_token_refresh_success_sends_message(tmp_path):
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, status=401)
    resp_mock.add(resp_mock.POST, KAKAO_TOKEN_URL, json={
        'access_token': 'new_access',
        'refresh_token': 'new_refresh',
        'expires_in': 21600,
    }, status=200)
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, json={'result_code': 0}, status=200)

    notifier = make_notifier(tmp_path)
    notifier.send(title='새 공지', body='테스트')

    assert notifier.secrets['access_token'] == 'new_access'
    assert notifier.secrets['refresh_token'] == 'new_refresh'

    # secrets.json에도 저장됐는지 확인
    saved = json.loads((tmp_path / 'secrets.json').read_text())
    assert saved['access_token'] == 'new_access'
    assert saved['refresh_token'] == 'new_refresh'


# T8: Kakao 401 → 갱신 실패 → RuntimeError
@resp_mock.activate
def test_token_refresh_failure_raises(tmp_path):
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, status=401)
    resp_mock.add(resp_mock.POST, KAKAO_TOKEN_URL, json={'error': 'KOE010'}, status=400)

    notifier = make_notifier(tmp_path)
    with pytest.raises(RuntimeError, match='Token refresh failed|Kakao token refresh failed'):
        notifier.send(title='새 공지', body='테스트')


# 갱신 시 새 refresh_token 없으면 기존 유지
@resp_mock.activate
def test_token_refresh_keeps_old_refresh_when_not_rotated(tmp_path):
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, status=401)
    resp_mock.add(resp_mock.POST, KAKAO_TOKEN_URL, json={
        'access_token': 'new_access',
        # refresh_token 미포함 → 기존 유지
        'expires_in': 21600,
    }, status=200)
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, json={'result_code': 0}, status=200)

    notifier = make_notifier(tmp_path)
    notifier.send(title='새 공지', body='테스트')

    assert notifier.secrets['access_token'] == 'new_access'
    assert notifier.secrets['refresh_token'] == 'old_refresh'


# client_secret 포함 여부 확인
@resp_mock.activate
def test_client_secret_sent_when_set(tmp_path):
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, status=401)

    captured_body = {}

    def token_callback(request):
        captured_body.update(dict(
            pair.split('=') for pair in request.body.split('&') if '=' in pair
        ))
        return (200, {}, json.dumps({
            'access_token': 'new_access',
            'expires_in': 21600,
        }))

    resp_mock.add_callback(resp_mock.POST, KAKAO_TOKEN_URL, token_callback,
                           content_type='application/json')
    resp_mock.add(resp_mock.POST, KAKAO_MSG_URL, json={'result_code': 0}, status=200)

    notifier = make_notifier(tmp_path, secrets={'kakao_client_secret': 'my_secret'})
    notifier.send(title='테스트', body='')

    assert captured_body.get('client_secret') == 'my_secret'
