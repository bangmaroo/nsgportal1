"""
카카오톡 '나에게 보내기' 알림 전송.

Kakao OAuth 토큰 갱신 흐름:
  POST /v2/api/talk/memo/default/send → 401
  → POST /oauth/token (refresh_token grant)
  → 새 access_token + (새 refresh_token 있으면) 저장
  → 재시도

주의: Kakao는 갱신 응답에 새 refresh_token을 포함할 수 있음.
반드시 새 refresh_token도 secrets.json에 저장해야 60일 이후에도 갱신 가능.
"""
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

KAKAO_TOKEN_URL = 'https://kauth.kakao.com/oauth/token'
KAKAO_MSG_URL = 'https://kapi.kakao.com/v2/api/talk/memo/default/send'


class KakaoNotifier:
    def __init__(self, secrets: dict, secrets_path: Path):
        self.secrets = secrets
        self.secrets_path = secrets_path

    def send(self, title: str, body: str, url: str = '', header: str = '[그룹웨어 새 글]') -> None:
        """카카오톡으로 텍스트 메시지를 나에게 전송한다. 401 시 토큰 갱신 후 1회 재시도."""
        payload = self._build_payload(title, body, url, header)

        resp = self._post_message(payload)
        if resp.status_code == 401:
            logger.info('Kakao access token expired — refreshing...')
            self._refresh_token()
            resp = self._post_message(payload)

        if resp.status_code != 200:
            raise RuntimeError(
                f'Kakao message send failed ({resp.status_code}): {resp.text}'
            )
        logger.info('Kakao notification sent: %s', title)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _post_message(self, payload: dict) -> requests.Response:
        return requests.post(
            KAKAO_MSG_URL,
            headers={'Authorization': f'Bearer {self.secrets["access_token"]}'},
            data=payload,
            timeout=10,
        )

    def _build_payload(self, title: str, body: str, url: str, header: str) -> dict:
        template = {
            'object_type': 'text',
            'text': f'{header}\n{title}\n{body}'.strip(),
            'link': {
                'web_url': url,
                'mobile_web_url': url,
            },
        }
        return {'template_object': json.dumps(template, ensure_ascii=False)}

    def _refresh_token(self) -> None:
        """리프레시 토큰으로 액세스 토큰을 갱신하고 새 토큰을 secrets.json에 원자적으로 저장한다."""
        params = {
            'grant_type': 'refresh_token',
            'client_id': self.secrets['kakao_rest_api_key'],
            'refresh_token': self.secrets['refresh_token'],
        }
        client_secret = self.secrets.get('kakao_client_secret', '').strip()
        if client_secret:
            params['client_secret'] = client_secret

        resp = requests.post(KAKAO_TOKEN_URL, data=params, timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(
                f'Kakao token refresh failed ({resp.status_code}): {resp.text}\n'
                'If error is KOE010, enable Client Secret in Kakao Developers '
                'and set kakao_client_secret in secrets.json.'
            )

        data = resp.json()
        self.secrets['access_token'] = data['access_token']
        # Kakao가 새 refresh_token을 발급한 경우 반드시 저장 (없으면 기존 유지)
        if 'refresh_token' in data:
            self.secrets['refresh_token'] = data['refresh_token']
        self.secrets['expires_at'] = int(time.time()) + data.get('expires_in', 21600)

        _write_json_atomic(self.secrets, self.secrets_path)
        logger.info('Kakao tokens refreshed and saved.')


def _write_json_atomic(data: dict, path: Path) -> None:
    """tempfile + os.replace() 를 이용한 원자적 JSON 쓰기."""
    dir_ = path.parent
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=dir_, suffix='.tmp', delete=False
    ) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_name = f.name

    os.replace(tmp_name, path)
