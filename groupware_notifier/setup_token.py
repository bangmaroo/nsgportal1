"""
카카오 OAuth 초기 토큰 발급 스크립트 (1회 수동 실행).

실행 방법:
    python groupware_notifier/setup_token.py

전제:
    1. https://developers.kakao.com 에서 앱 생성
    2. [카카오 로그인] 활성화
    3. [동의항목] → 카카오톡 메시지 전송 (talk_message) 활성화
    4. [플랫폼] → Web 추가, 사이트 도메인에 https://example.com 등록
    5. [보안] → Client secret 사용 여부 확인 후 secrets.json에 기입 (선택)
    6. secrets.json에 kakao_rest_api_key 입력 후 이 스크립트 실행

    Kakao Developers redirect URI 설정:
        https://example.com/oauth  (아래 REDIRECT_URI 와 동일해야 함)
"""
import json
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

REDIRECT_URI = 'https://example.com/oauth'
KAKAO_AUTH_URL = 'https://kauth.kakao.com/oauth/authorize'
KAKAO_TOKEN_URL = 'https://kauth.kakao.com/oauth/token'

BASE_DIR = Path(__file__).parent
SECRETS_PATH = BASE_DIR / 'secrets.json'

SECRETS_TEMPLATE = {
    'groupware_username': '',
    'groupware_password': '',
    'kakao_rest_api_key': '',
    'kakao_client_secret': '',
    'access_token': '',
    'refresh_token': '',
    'expires_at': 0,
}


def main() -> None:
    # secrets.json 로드 또는 생성
    if not SECRETS_PATH.exists():
        print(f'secrets.json 없음 → {SECRETS_PATH} 생성 중...')
        _write_json(SECRETS_TEMPLATE, SECRETS_PATH)
        print('secrets.json 생성 완료. 파일을 열어 정보를 입력한 뒤 다시 실행하세요.')
        sys.exit(0)

    with open(SECRETS_PATH, encoding='utf-8') as f:
        secrets = json.load(f)

    rest_api_key = secrets.get('kakao_rest_api_key', '').strip()
    if not rest_api_key:
        rest_api_key = input('Kakao REST API Key를 입력하세요: ').strip()
        if not rest_api_key:
            print('REST API Key가 필요합니다.')
            sys.exit(1)
        secrets['kakao_rest_api_key'] = rest_api_key

    auth_url = (
        f'{KAKAO_AUTH_URL}'
        f'?client_id={rest_api_key}'
        f'&redirect_uri={REDIRECT_URI}'
        f'&response_type=code'
        f'&scope=talk_message'
    )

    print('\n브라우저에서 카카오 로그인 페이지를 엽니다...')
    print(f'\n아래 URL을 직접 열어도 됩니다:\n{auth_url}\n')
    webbrowser.open(auth_url)

    print(f'로그인 완료 후 리다이렉트된 URL 전체를 붙여넣으세요.')
    print(f'(예: {REDIRECT_URI}?code=XXXXX)\n')
    redirect_url = input('리다이렉트 URL: ').strip()

    code = _extract_code(redirect_url)
    if not code:
        print('오류: URL에서 code 파라미터를 추출할 수 없습니다.')
        sys.exit(1)

    tokens = _exchange_code(rest_api_key, code, secrets.get('kakao_client_secret', ''))
    secrets['access_token'] = tokens['access_token']
    secrets['refresh_token'] = tokens['refresh_token']
    secrets['expires_at'] = int(time.time()) + tokens.get('expires_in', 21600)

    _write_json(secrets, SECRETS_PATH)
    print('\n✅ 토큰이 secrets.json에 저장되었습니다. 이제 main.py를 실행할 수 있습니다.')


def _extract_code(url: str) -> str | None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    codes = params.get('code', [])
    return codes[0] if codes else None


def _exchange_code(client_id: str, code: str, client_secret: str) -> dict:
    params = {
        'grant_type': 'authorization_code',
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'code': code,
    }
    if client_secret.strip():
        params['client_secret'] = client_secret

    resp = requests.post(KAKAO_TOKEN_URL, data=params, timeout=10)
    if resp.status_code != 200:
        print(f'토큰 발급 오류 ({resp.status_code}): {resp.text}')
        sys.exit(1)

    return resp.json()


def _write_json(data: dict, path: Path) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    main()
