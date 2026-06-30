# -*- coding: utf-8 -*-
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
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    # 알림 미전송 확인
    mock_notifier.send.assert_not_called()

    # state.json 생성 확인
    state = json.loads(state_path.read_text(encoding='utf-8'))
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
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    # 새 글 2개만 알림 전송
    assert mock_notifier.send.call_count == 2
    calls = [c.kwargs['title'] for c in mock_notifier.send.call_args_list]
    assert '새 공지1' in calls
    assert '새 공지2' in calls

    # state 업데이트 확인
    state = json.loads(state_path.read_text(encoding='utf-8'))
    assert state['boards']['notice']['last_seen_id'] == 102


# ── T3: 새 게시물 없음 + 오늘 이미 heartbeat 전송 → 조용히 종료 ──────────────

def test_no_new_posts_no_notification(tmp_path):
    """오늘 이미 heartbeat를 보낸 경우 추가 알림 없음."""
    from datetime import date
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({
        'boards': {'notice': {'last_seen_id': 102}},
        'heartbeat_last_sent': date.today().isoformat(),
    }))

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
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    mock_notifier.send.assert_not_called()


# ── T5: Heartbeat ─────────────────────────────────────────────────────────────

def _base_config_and_secrets():
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
    return config, secrets


def test_heartbeat_sent_when_no_new_posts_and_not_sent_today(tmp_path):
    """새 게시물 없고 오늘 heartbeat 미전송 → heartbeat 알림 전송."""
    from datetime import date
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({
        'boards': {'notice': {'last_seen_id': 102}},
        'heartbeat_last_sent': '2000-01-01',  # 과거 날짜
    }))

    config, secrets = _base_config_and_secrets()
    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [{'id': 102, 'title': '기존 공지'}]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    mock_notifier.send.assert_called_once()
    call_kwargs = mock_notifier.send.call_args.kwargs
    assert call_kwargs['header'] == '💓 Heartbeat'
    assert call_kwargs['title'] == '✅ 정상 동작 중'

    # heartbeat_last_sent가 오늘로 갱신됐는지 확인
    state = json.loads(state_path.read_text(encoding='utf-8'))
    assert state['heartbeat_last_sent'] == date.today().isoformat()


def test_heartbeat_not_sent_when_already_sent_today(tmp_path):
    """오늘 이미 heartbeat를 전송한 경우 중복 전송 안 함."""
    from datetime import date
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({
        'boards': {'notice': {'last_seen_id': 102}},
        'heartbeat_last_sent': date.today().isoformat(),
    }))

    config, secrets = _base_config_and_secrets()
    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [{'id': 102, 'title': '기존 공지'}]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    mock_notifier.send.assert_not_called()


def test_heartbeat_not_sent_when_new_posts_exist(tmp_path):
    """새 게시물 알림이 있으면 heartbeat 대신 게시물 알림만 전송."""
    from datetime import date
    state_path = tmp_path / 'state.json'
    state_path.write_text(json.dumps({
        'boards': {'notice': {'last_seen_id': 100}},
        'heartbeat_last_sent': '2000-01-01',
    }))

    config, secrets = _base_config_and_secrets()
    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [
        {'id': 101, 'title': '새 공지'},
        {'id': 100, 'title': '기존 공지'},
    ]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    # 게시물 알림 1회만 전송, heartbeat 없음
    assert mock_notifier.send.call_count == 1
    assert mock_notifier.send.call_args.kwargs['header'] == '📬 새 게시물'

    # heartbeat_last_sent가 오늘로 갱신됐는지 확인 (게시물 알림이 생존 신호 역할)
    state = json.loads(state_path.read_text(encoding='utf-8'))
    assert state['heartbeat_last_sent'] == date.today().isoformat()


def test_heartbeat_not_sent_on_first_run(tmp_path):
    """첫 실행(기준점 설정 단계)에서는 heartbeat 미전송."""
    state_path = tmp_path / 'state.json'
    # state.json 없음 = 첫 실행

    config, secrets = _base_config_and_secrets()
    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [{'id': 100, 'title': '기존 공지'}]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    mock_notifier.send.assert_not_called()


# ── T4: 경조사 이모티콘 분기 테스트 ─────────────────────────────────────────

def test_condolence_and_congratulation_emojis(tmp_path):
    state_path = tmp_path / 'state.json'
    # 이전 상태: last_seen_id = 100
    state_path.write_text(json.dumps({'boards': {'BB140304938548009': {'last_seen_id': 100}}}))

    config = {
        'groupware_url': 'https://gw.example.com',
        'login_url': 'https://gw.example.com/login',
        'board_ids': ['BB140304938548009'],  # 경조사 게시판 ID
        'post_selector': 'tr[data-id]',
        'post_id_attr': 'data-id',
        'post_title_selector': 'td.title a',
        'board_names': {'BB140304938548009': '경조사'},
    }
    secrets = {
        'groupware_username': 'u', 'groupware_password': 'p',
        'kakao_rest_api_key': 'key', 'kakao_client_secret': '',
        'access_token': 'token', 'refresh_token': 'refresh',
        'expires_at': int(time.time()) + 3600,
    }

    mock_scraper = MagicMock()
    mock_scraper.get_posts.return_value = [
        {'id': 103, 'title': '회원 결혼 알림'},      # 기쁜 일 -> 🎉
        {'id': 102, 'title': 'OOO 부친 부고'},      # 슬픈 일 -> 🕯️
        {'id': 101, 'title': '경조사 알림'},        # 기타 -> 🎊
        {'id': 100, 'title': '기존 글'},
    ]
    mock_notifier = MagicMock()

    with (
        patch('groupware_notifier.main.STATE_PATH', state_path),
        patch('groupware_notifier.main._load_json', side_effect=lambda p, n: config if 'config' in str(p) else secrets),
        patch('groupware_notifier.main.GroupwareScraper', return_value=mock_scraper),
        patch('groupware_notifier.main.build_notifier', return_value=mock_notifier),
    ):
        run()

    # 3개의 새 글에 대해 알림 발송 확인
    assert mock_notifier.send.call_count == 3
    
    # call_args_list에서 body 인자를 확인하여 적절한 이모티콘이 적용되었는지 확인
    # body 포맷: f'{current_emoji} {board_name}'
    calls = mock_notifier.send.call_args_list
    
    # id 101 ('경조사 알림' -> 기본값 '🎊')
    # id 102 ('OOO 부친 부고' -> 슬픈 일 '🕯️')
    # id 103 ('회원 결혼 알림' -> 기쁜 일 '🎉')
    # main.py에서 정렬하여 호출하므로 호출 순서는 101, 102, 103 순서
    
    # 101번 글
    assert calls[0].kwargs['title'] == '경조사 알림'
    assert '🎊' in calls[0].kwargs['body']
    
    # 102번 글
    assert calls[1].kwargs['title'] == 'OOO 부친 부고'
    assert '🕯️' in calls[1].kwargs['body']
    
    # 103번 글
    assert calls[2].kwargs['title'] == '회원 결혼 알림'
    assert '🎉' in calls[2].kwargs['body']
