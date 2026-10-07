import secrets
import time

from fastapi import HTTPException, Request

from . import config

# IP별 로그인 실패 기록 (메모리). 10분 안에 5회 실패하면 잠시 차단
_FAILS: dict[str, list[float]] = {}
WINDOW, MAX_FAILS = 600, 5


def _recent(ip: str) -> list[float]:
    now = time.time()
    _FAILS[ip] = [t for t in _FAILS.get(ip, []) if now - t < WINDOW]
    return _FAILS[ip]


def is_locked(ip: str) -> bool:
    return len(_recent(ip)) >= MAX_FAILS


def check_credentials(ip: str, user: str, password: str) -> bool:
    ok_id = secrets.compare_digest(user.encode(), config.ADMIN_ID.encode())
    ok_pw = secrets.compare_digest(password.encode(), config.ADMIN_PASSWORD.encode())
    if ok_id and ok_pw:
        _FAILS.pop(ip, None)
        return True
    _recent(ip).append(time.time())
    return False


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("admin"))


def require_admin(request: Request):
    """API용 의존성: 로그인 안 했으면 401"""
    if not is_logged_in(request):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
