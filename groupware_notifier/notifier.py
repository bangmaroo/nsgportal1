"""
카카오톡·디스코드 알림 전송.

KakaoNotifier   - 카카오톡 '나에게 보내기' (OAuth, 토큰 자동 갱신)
DiscordNotifier - 디스코드 웹훅 (secrets.json의 discord_webhook_url)
MultiNotifier   - 위 두 알림을 한 번에 전송; 개별 실패는 로그만 남기고 계속 진행

Kakao OAuth 토큰 갱신 흐름:
  POST /v2/api/talk/memo/default/send → 401
  → POST /oauth/token (refresh_token grant)
  → 새 access_token + (새 refresh_token 있으면) 저장
  → 재시도
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

    def send(self, title: str, body: str, url: str = '', header: str = '📬 새 게시물') -> None:
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
        divider = '─' * 20
        parts = [header, divider, title]
        if body:
            parts.append(body)
        if url:
            parts += [divider, f'🔗 {url}']
        template = {
            'object_type': 'text',
            'text': '\n'.join(parts),
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


class DiscordNotifier:
    # 헤더별 embed 색상
    _COLORS = {
        '📬 새 게시물': 0x5865F2,   # 디스코드 블루
        '🍽️ 식단 알림':  0xF6A623,   # 오렌지
        '💓 Heartbeat':  0x57F287,   # 초록
    }
    _DEFAULT_COLOR = 0x5865F2

    def __init__(self, webhook_url: str):
        self._url = webhook_url

    def send(self, title: str, body: str, url: str = '', header: str = '📬 새 게시물') -> None:
        color = self._COLORS.get(header, self._DEFAULT_COLOR)
        embed: dict = {
            'author': {'name': header},
            'title': title,
            'description': body,
            'color': color,
        }
        if url:
            embed['url'] = url

        payload = {
            'username': '그룹웨어 알림',
            'embeds': [embed],
        }
        resp = requests.post(self._url, json=payload, timeout=10)
        if resp.status_code not in (200, 204):
            raise RuntimeError(
                f'Discord webhook 전송 실패 ({resp.status_code}): {resp.text}'
            )
        logger.info('Discord notification sent: %s', title)


class MultiNotifier:
    """등록된 모든 알림 수단으로 메시지를 전송한다. 개별 실패는 로그만 남긴다."""

    def __init__(self, notifiers: list):
        self._notifiers = notifiers

    def send(self, title: str, body: str, url: str = '', header: str = '📬 새 게시물') -> None:
        for notifier in self._notifiers:
            try:
                notifier.send(title=title, body=body, url=url, header=header)
            except Exception as e:
                logger.error('%s 전송 실패: %s', type(notifier).__name__, e)


def build_notifier(secrets: dict, secrets_path: Path, config: dict | None = None) -> MultiNotifier:
    """config의 notifiers 목록에 따라 알림 수단을 활성화해 MultiNotifier로 반환한다.

    config.json 예시:
        "notifiers": ["kakao", "discord"]   ← 둘 다
        "notifiers": ["discord"]            ← 디스코드만
        "notifiers": ["kakao"]              ← 카카오톡만
    notifiers 키가 없으면 secrets.json에 값이 있는 수단을 모두 활성화한다.
    """
    enabled: list[str] | None = (config or {}).get('notifiers')
    notifiers = []

    if enabled is None or 'kakao' in enabled:
        if secrets.get('access_token'):
            notifiers.append(KakaoNotifier(secrets, secrets_path))

    if enabled is None or 'discord' in enabled:
        webhook_url = secrets.get('discord_webhook_url', '').strip()
        if webhook_url:
            notifiers.append(DiscordNotifier(webhook_url))

    if not notifiers:
        logger.warning('활성화된 알림 수단이 없습니다. config.json의 notifiers와 secrets.json을 확인하세요.')

    return MultiNotifier(notifiers)


def _write_json_atomic(data: dict, path: Path) -> None:
    """tempfile + os.replace() 를 이용한 원자적 JSON 쓰기."""
    dir_ = path.parent
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=dir_, suffix='.tmp', delete=False
    ) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        tmp_name = f.name

    os.replace(tmp_name, path)
