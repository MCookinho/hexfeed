"""
hexfeed - brute-force login protection module
Extraído de main.py para evitar importação circular.
"""

import time
from collections import defaultdict

_login_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW = 300  # 5 minutes
LOGIN_LOCKOUT = 900  # 15 minutes
_login_lockouts: dict[str, float] = {}


def check_login_bruteforce(username: str) -> bool:
    """Returns True if the username is temporarily locked out."""
    now = time.time()

    if username in _login_lockouts:
        if now < _login_lockouts[username]:
            return True
        del _login_lockouts[username]

    attempts = _login_attempts[username]
    while attempts and attempts[0] < now - LOGIN_WINDOW:
        attempts.pop(0)

    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        _login_lockouts[username] = now + LOGIN_LOCKOUT
        return True

    return False


def record_login_attempt(username: str, success: bool):
    if not success:
        _login_attempts[username].append(time.time())
    else:
        _login_attempts.pop(username, None)
        _login_lockouts.pop(username, None)
