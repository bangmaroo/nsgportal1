"""main.py 단위 테스트 — state 관리, lockfile, 첫 실행, 정상/비정상 흐름."""
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from groupware_notifier.main import (
    acquire_lock,
    load_state,
    release_lock,
    run,
    save_state,
)


# ── State 관리 ────────────────────────────────────────────────────────────────

def test_load_state_returns_empty_when_missing(tmp_path):
    with patch('groupware_notifier.main.STATE_PATH', tmp_path / 'state.json'):
        state = load_state()
    assert state == {}


def test_save_and_load_state_roundtrip(tmp_path):
    state_path = tmp_path / 'state.json'
    data = {'boards': {'notice': {'last_seen_id': 42}}}

    with patch('groupware_notifier.main.STATE_PATH', state_path):
        save_state(data)
        loaded = load_state()

    assert loaded == data


def test_save_state_is_atomic(tmp_path):
    """쓰기 후 tmp 파일이 남아 있지 않음을 확인."""
    state_path = tmp_path / 'state.json'
    with patch('groupware_notifier.main.STATE_PATH', state_path):
        save_state({'boards': {}})

    tmp_files = list(tmp_path.glob('*.tmp'))
    assert tmp_files == []


# ── Lockfile ──────────────────────────────────────────────────────────────────

def test_acquire_lock_creates_file(tmp_path):
    lock_path = tmp_path / 'run.lock'
    with patch('groupware_notifier.main.LOCKFILE_PATH', lock_path):
        result = acquire_lock()
        assert result is True
        assert lock_path.exists()
        assert int(lock_path.read_text()) == os.getpid()
        release_lock()


def test_acquire_lock_blocks_second_instance(tmp_path):
    lock_path = tmp_path / 'run.lock'
    lock_path.write_text(str(os.getpid()))  # 현재 프로세스 PID로 잠금

    with patch('groupware_notifier.main.LOCKFILE_PATH', lock_path):
        result = acquire_lock()

    assert result is False


def test_acquire_lock_cleans_stale(tmp_path):
    lock_path = tmp_path / 'run.lock'
    lock_path.write_text('99999999')  # 존재하지 않는 PID

    with patch('groupware_notifier.main.LOCKFILE_PATH', lock_path):
        result = acquire_lock()
        assert result is True
        release_lock()


# ── T1: 첫 실행 (state.json 없음) → 알림 없음 ─────────────────────────────────

def test_first_run_no_alerts(tmp_path):
    state_path = tmp_path / 'state.json'
    config = {
        'groupware_url': 'https://gw.example.com',
        'login_url': 'https://gw.example.com/login',
        'board_ids': ['notice'],
        'post_selector': 'tr[data-id]',
        'post_id_attr': 'data-id',
        'post_title_selector': 'td.title a',
    }
    secrets = {
        'groupware_username': 'u',
        'groupware_password': 'p',
        'kakao_rest_api_key': 'key',
        'kakao_client_secret': '',
        'access_token': 'token',
        'refresh_token': 'refresh',
        'expires_at': int(time.time()) + 3600,
    }

    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [
        {'id': 100, 'title': '기존 공지'},
        {'id': 101, 'title': '기존 공지2'},
    ]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.CONFIG_PATH', tmp_path / 'config.json'),
        patch('groupware_notifier.main.SECRETS_PATH', tmp_path / 'secrets.json'),
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.KakaoNotifier', return_value=mock_notifier),
    ):
        run()

    # 알림 미전송 확인
    mock_notifier.send.assert_not_called()

    # state.json 생성 확인
    state = json.loads(state_path.read_text())
    assert state['boards']['notice']['last_seen_id'] == 101


# ── T2: 새 게시물 있음 → 알림 전송 ───────────────────────────────────────────

def test_new_posts_sends_notifications(tmp_path):
    state_path = tmp_path / 'state.json'
    # 이전 상태: last_seen_id = 100
    state_path.write_text(json.dumps({'boards': {'notice': {'last_seen_id': 100}}}))

    config = {
        'groupware_url': 'https://gw.example.com',
        'login_url': 'https://gw.example.com/login',
        'board_ids': ['notice'],
        'post_selector': 'tr[data-id]',
        'post_id_attr': 'data-id',
        'post_title_selector': 'td.title a',
    }
    secrets = {
        'groupware_username': 'u', 'groupware_password': 'p',
        'kakao_rest_api_key': 'key', 'kakao_client_secret': '',
        'access_token': 'token', 'refresh_token': 'refresh',
        'expires_at': int(time.time()) + 3600,
    }

    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [
        {'id': 102, 'title': '새 공지2'},
        {'id': 101, 'title': '새 공지1'},
        {'id': 100, 'title': '기존 공지'},
    ]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.KakaoNotifier', return_value=mock_notifier),
    ):
        run()

    # 새 글 2개만 알림 전송
    assert mock_notifier.send.call_count == 2
    calls = [c.kwargs['title'] for c in mock_notifier.send.call_args_list]
    assert '새 공지1' in calls
    assert '새 공지2' in calls

    # state 업데이트 확인
    state = json.loads(state_path.read_text())
    assert state['boards']['notice']['last_seen_id'] == 102


# ── T3: 새 게시물 없음 → 조용히 종료 ─────────────────────────────────────────

def test_no_new_posts_no_notification(tmp_path):
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({'boards': {'notice': {'last_seen_id': 102}}}))

    config = {
        'groupware_url': 'https://gw.example.com', 'login_url': 'https://gw.example.com/login',
        'board_ids': ['notice'], 'post_selector': 'tr[data-id]',
        'post_id_attr': 'data-id', 'post_title_selector': 'td.title a',
    }
    secrets = {
        'groupware_username': 'u', 'groupware_password': 'p',
        'kakao_rest_api_key': 'key', 'kakao_client_secret': '',
        'access_token': 'token', 'refresh_token': 'refresh',
        'expires_at': int(time.time()) + 3600,
    }

    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [
        {'id': 102, 'title': '기존 공지'},
    ]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.KakaoNotifier', return_value=mock_notifier),
    ):
        run()

    mock_notifier.send.assert_not_called()
