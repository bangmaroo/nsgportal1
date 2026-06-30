"""
그룹웨어 새 게시물 감지 및 식단 알림 → 카카오톡 알림 메인 스크립트.

실행 방법:
    python groupware_notifier/main.py            # 새 게시물 확인
    python groupware_notifier/main.py --meal     # 금일 식단 알림 (평일 오전 10시 예약)

Windows Task Scheduler 등록 시:
    프로그램: C:\\path\\to\\.venv\\Scripts\\python.exe
    인수(새 글): groupware_notifier\\main.py
    인수(식단):  groupware_notifier\\main.py --meal
    시작 위치:   C:\\path\\to\\nsgportal1
"""
import argparse
import json
import logging
import os
import sys
import tempfile
import time
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (python groupware_notifier/main.py 방식으로 실행 시 필요)
sys.path.insert(0, str(Path(__file__).parent.parent))

import psutil

from groupware_notifier.notifier import build_notifier, _write_json_atomic
from groupware_notifier.scraper import GroupwareScraper

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / 'config.json'
SECRETS_PATH = BASE_DIR / 'secrets.json'
STATE_PATH = BASE_DIR / 'state.json'
LOCKFILE_PATH = BASE_DIR / 'run.lock'
LOG_DIR = BASE_DIR.parent / 'logs'


# ── 로깅 설정 ──────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    # Windows 콘솔에서 cp949 인코딩 에러 방지
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    LOG_DIR.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_DIR / 'run.log',
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding='utf-8',
    )
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    file_handler.setFormatter(fmt)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
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
        with open(STATE_PATH, encoding='utf-8-sig') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.getLogger(__name__).error('state.json 로드 실패: %s. 빈 상태로 초기화합니다.', e)
        return {}


def save_state(state: dict) -> None:
    """state.json 원자적 쓰기 (tempfile + os.replace)."""
    _write_json_atomic(state, STATE_PATH)


def _get_congratulatory_or_condolence_emoji(title: str) -> str:
    """경조사 제목의 키워드를 분석하여 기쁜 일(🎉)과 슬픈 일(🕯️)을 구분하고 이모티콘을 반환한다."""
    condolence_keywords = ['부고', '별세', '소천', '영면', '장례', '상주', '득상', '조의']
    congratulatory_keywords = ['결혼', '화혼', '청첩', '득남', '득녀', '출산', '축하', '돌잔치', '회갑', '고희']

    for kw in condolence_keywords:
        if kw in title:
            return '🕯️'

    for kw in congratulatory_keywords:
        if kw in title:
            return '🎉'

    return '🎊'


# ── 메인 로직 ─────────────────────────────────────────────────────────────────

def run() -> None:
    logger = logging.getLogger(__name__)
    config = _load_json(CONFIG_PATH, 'config.json')
    secrets = _load_json(SECRETS_PATH, 'secrets.json')

    scraper = GroupwareScraper(config, secrets)
    notifier = build_notifier(secrets, SECRETS_PATH, config)

    state = load_state()
    is_first_run = not state
    if is_first_run:
        logger.info('첫 실행 — 현재 게시물을 기준점으로 설정합니다. 이번 실행에서는 알림이 없습니다.')

    board_emojis = {
        'BB140533555033482': '📌',  # 공지사항
        'BB140304938548009': '🎊',  # 경조사
        'BB140306311362185': '👥',  # 인사발령
        'BB168050962738658': '💼',  # NDS 수주정보
        'BB140306307605625': '📚',  # 교육세미나일정
    }

    state.setdefault('boards', {})
    new_state = {'boards': dict(state['boards'])}
    if 'heartbeat_last_sent' in state:
        new_state['heartbeat_last_sent'] = state['heartbeat_last_sent']
    any_new_posts = False

    for board_id in config.get('board_ids', []):
        posts = scraper.get_posts(board_id)
        if not posts:
            continue

        max_id = max(p['id'] for p in posts)
        board_state = state['boards'].get(board_id, {})
        last_seen = board_state.get('last_seen_id', 0)
        seen_titles = board_state.get('seen_titles', {})

        current_titles = {str(p['id']): p['title'] for p in posts}

        if is_first_run:
            new_state['boards'][board_id] = {
                'last_seen_id': max_id,
                'seen_titles': current_titles,
            }
            logger.info('[%s] 기준점 설정: last_seen_id=%d', board_id, max_id)
            continue

        board_name = config.get('board_names', {}).get(board_id, board_id)
        board_emoji = board_emojis.get(board_id, '📋')

        new_posts = sorted(
            [p for p in posts if p['id'] > last_seen],
            key=lambda p: p['id'],
        )
        for post in new_posts:
            current_emoji = board_emoji
            if board_id == 'BB140304938548009':
                current_emoji = _get_congratulatory_or_condolence_emoji(post['title'])
            try:
                notifier.send(
                    title=post['title'],
                    body=f'{current_emoji} {board_name}',
                    header='📬 새 게시물',
                )
                any_new_posts = True
                logger.info('[%s] 새 게시물 알림: id=%d "%s"', board_name, post['id'], post['title'])
            except Exception as e:
                logger.error('[%s] 알림 전송 실패: %s', board_id, e)
                raise

        for post in posts:
            if post['id'] > last_seen:
                continue
            old_title = seen_titles.get(str(post['id']))
            if old_title is not None and old_title != post['title']:
                current_emoji = board_emoji
                if board_id == 'BB140304938548009':
                    current_emoji = _get_congratulatory_or_condolence_emoji(post['title'])
                try:
                    notifier.send(
                        title=post['title'],
                        body=f'{current_emoji} {board_name}\n이전 제목: {old_title}',
                        header='✏️ 게시물 수정',
                    )
                    any_new_posts = True
                    logger.info('[%s] 제목 변경 알림: id=%d "%s" → "%s"', board_name, post['id'], old_title, post['title'])
                except Exception as e:
                    logger.error('[%s] 알림 전송 실패: %s', board_id, e)
                    raise

        new_state['boards'][board_id] = {
            'last_seen_id': max_id,
            'seen_titles': current_titles,
        }

    today_str = date.today().isoformat()

    if not is_first_run:
        if any_new_posts:
            # 새 게시물 알림이 오늘의 생존 신호 역할을 하므로 heartbeat 전송 불필요
            new_state['heartbeat_last_sent'] = today_str
        elif state.get('heartbeat_last_sent') != today_str and datetime.now().hour >= 18:
            # 오후 6시 이후 실행에서 하루 동안 알림이 없었으면 heartbeat 전송
            try:
                notifier.send(
                    title='✅ 정상 동작 중',
                    body='새 게시물이 없습니다.',
                    header='💓 Heartbeat',
                )
                new_state['heartbeat_last_sent'] = today_str
                logger.info('Heartbeat 알림 전송 완료.')
            except Exception as e:
                logger.error('Heartbeat 알림 전송 실패: %s', e)

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


# ── 식단 알림 ─────────────────────────────────────────────────────────────────

def send_meal() -> None:
    """금일 식단을 카카오톡으로 전송한다."""
    logger = logging.getLogger(__name__)
    config = _load_json(CONFIG_PATH, 'config.json')
    secrets = _load_json(SECRETS_PATH, 'secrets.json')

    scraper = GroupwareScraper(config, secrets)
    notifier = build_notifier(secrets, SECRETS_PATH, config)

    menu = scraper.get_today_menu()
    if not menu or not menu.get('lunch'):
        logger.info('오늘 중식 식단 정보가 없습니다.')
        return

    today = date.today()
    day_names = ['월', '화', '수', '목', '금', '토', '일']
    day_name = day_names[today.weekday()]
    date_str = today.strftime(f'%m/%d({day_name})')

    lines = [f'☀️ 중식\n{menu["lunch"]}']
    if menu.get('lunch_kcal'):
        lines.append(f'🔥 {menu["lunch_kcal"]}')
    if menu.get('dinner'):
        lines.append(f'\n🌙 석식\n{menu["dinner"]}')

    notifier.send(
        title=f'🍱 {date_str} 오늘의 식단',
        body='\n'.join(lines),
        header='🍽️ 식단 알림',
    )
    logger.info('식단 알림 전송 완료')


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='그룹웨어 알림')
    parser.add_argument('--meal', action='store_true', help='금일 식단 알림 전송')
    args = parser.parse_args()

    setup_logging()

    if args.meal:
        try:
            send_meal()
        except Exception as e:
            logging.getLogger(__name__).error('식단 알림 오류: %s', e, exc_info=True)
            sys.exit(1)
        return

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
