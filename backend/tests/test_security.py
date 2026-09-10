"""security.py 单元测试：滑动窗口限流。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.security import SlidingWindowLimiter


def test_limiter_allows_up_to_limit():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("ip", 2) is True
    assert limiter.allow("ip", 2) is True
    assert limiter.allow("ip", 2) is False


def test_limiter_isolated_per_key():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("a", 1) is True
    assert limiter.allow("b", 1) is True
    assert limiter.allow("a", 1) is False


def test_limiter_reset():
    limiter = SlidingWindowLimiter()
    limiter.allow("ip", 1)
    limiter.reset()
    assert limiter.allow("ip", 1) is True
