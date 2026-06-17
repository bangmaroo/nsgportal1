"""
그룹웨어 새 게시물 감지 → 카카오톡 알림 메인 스크립트.

실행 방법:
    python groupware_notifier/main.py

Windows Task Scheduler 등록 시:
    프로그램: C:\\path\\to\\.venv\\Scripts\\python.exe   ← venv python.exe 경로 지정!
    인수:     groupware_notifier\\main.py
    시작 위치: C:\\path\\to\\nsgportal1

    주의: 'python main.py' 대신 venv의 python.exe 전체 경로를 사용해야
    requests / beautifulsoup4 등이 제대로 임포트됩니다.
"""
import json
import logging
import os
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import psutil

from groupware_notifier.notifier import KakaoNotifier, _write_json_atomic
from groupware_notifier.scraper import GroupwareScraper

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'config.json'
SECRETS_PATH = BASE_DIR / 'secrets.json'
STATE_PATH = BASE_DIR / 'state.json'
LOCKFILE_PATH = BASE_DIR / 'run.lock'
LOG_DIR = BASE_DIR.parent / 'logs'


# ── 로깅 설정 ──────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / 'run.log',
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding='utf-8',
    )
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    )
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, logging.StreamHandler(sys.stdout)],
    )


# ── PID 기반 lockfile ──────────────────────────────────────────────────────────

def acquire_lock() -> bool:
    """
    lockfile에 현재 PID를 기록하고 True 반환.
    다른 인스턴스가 실행 중이면 False 반환.
    크래시로 남은 stale lockfile은 PID 존재 여부로 자동 감지 후 삭제.
    """
    if LOCKFILE_PATH.exists():
        try:
            pid = int(LOCKFILE_PATH.read_text().strip())
            if psutil.pid_exists(pid):
                logging.getLogger(__name__).info(
                    'Another instance is running (PID %d). Exiting.', pid
                )
                return False
            logging.getLogger(__name__).warning(
                'Stale lockfile (PID %d no longer exists). Cleaning up.', pid
            )
            LOCKFILE_PATH.unlink()
        except (ValueError, OSError):
            LOCKFILE_PATH.unlink(missing_ok=True)

    LOCKFILE_PATH.write_text(str(os.getpid()))
    return True


def release_lock() -> None:
    LOCKFILE_PATH.unlink(missing_ok=True)


# ── State 관리 ────────────────────────────────────────────────────────────────

def load_state() -> dict:
    """state.json 로드. 없으면 빈 dict 반환 (첫 실행)."""
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.getLogger(__name__).error('state.json 로드 실패: %s. 빈 상태로 초기화합니다.', e)
        return {}


def save_state(state: dict) -> None:
    """state.json 원자적 쓰기 (tempfile + os.replace)."""
    _write_json_atomic(state, STATE_PATH)


# ── 메인 로직 ─────────────────────────────────────────────────────────────────

def run() -> None:
    logger = logging.getLogger(__name__)
    config = _load_json(CONFIG_PATH, 'config.json')
    secrets = _load_json(SECRETS_PATH, 'secrets.json')

    scraper = GroupwareScraper(config, secrets)
    notifier = KakaoNotifier(secrets, SECRETS_PATH)

    state = load_state()
    is_first_run = not state
    if is_first_run:
        logger.info('첫 실행 — 현재 게시물을 기준점으로 설정합니다. 이번 실행에서는 알림이 없습니다.')

    state.setdefault('boards', {})
    new_state = {'boards': dict(state['boards'])}
    any_new_posts = False

    for board_id in config.get('board_ids', []):
        posts = scraper.get_posts(board_id)
        if not posts:
            continue

        max_id = max(p['id'] for p in posts)
        last_seen = state['boards'].get(board_id, {}).get('last_seen_id', 0)

        if is_first_run:
            new_state['boards'][board_id] = {'last_seen_id': max_id}
            logger.info('[%s] 기준점 설정: last_seen_id=%d', board_id, max_id)
            continue

        new_posts = sorted(
            [p for p in posts if p['id'] > last_seen],
            key=lambda p: p['id'],
        )

        for post in new_posts:
            try:
                notifier.send(
                    title=post['title'],
                    body=f'게시판: {board_id}',
                )
                any_new_posts = True
                logger.info('[%s] 알림 전송: id=%d "%s"', board_id, post['id'], post['title'])
            except Exception as e:
                logger.error('[%s] 알림 전송 실패: %s', board_id, e)
                raise

        new_state['boards'][board_id] = {'last_seen_id': max_id}

    save_state(new_state)
    if not any_new_posts and not is_first_run:
        logger.info('새 게시물 없음.')


def _load_json(path: Path, name: str) -> dict:
    if not path.exists():
        print(f'오류: {name} 파일이 없습니다. {path}')
        print('config.example.json을 복사해 config.json을 만들고, secrets.json은 setup_token.py로 생성하세요.')
        sys.exit(1)
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging()
    if not acquire_lock():
        sys.exit(0)
    try:
        run()
    except Exception as e:
        logging.getLogger(__name__).error('실행 중 오류 발생: %s', e, exc_info=True)
        sys.exit(1)
    finally:
        release_lock()


if __name__ == '__main__':
    main()
