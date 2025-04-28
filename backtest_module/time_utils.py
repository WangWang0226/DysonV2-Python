# time_utils.py
from datetime import datetime, timezone, timedelta
from typing import Union
import random
import time

__all__ = [
    "tm",  # 全域單例
    "compute_due_time_and_duration",
    "random_time_in_day",
]


# ---------- TimeManager ----------
class TimeManager:
    _instance = None

    def __new__(cls):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._mock_ts = None
            cls._instance._tz = timezone.utc
        return cls._instance

    # --- setters / getters ---
    def setCurrentTime(self, ts: Union[int, float, datetime]):
        self._mock_ts = ts.timestamp() if isinstance(ts, datetime) else ts

    def getCurrentTime(self) -> datetime:
        if self._mock_ts is not None:
            return datetime.fromtimestamp(self._mock_ts, tz=self._tz)
        return datetime.now(tz=self._tz)

    def getTimestamp(self) -> float:
        return self.getCurrentTime().timestamp()

    def resetMock(self):  # 測試結束還原
        self._mock_ts = None


tm = TimeManager()  # <<— 全域單例


# ---------- helper ----------
def random_time_in_day(yyyy_mm_dd: datetime) -> datetime:
    """給定『某天的 00:00』，回傳當天隨機 hh:mm:ss 的 UTC datetime"""
    rand_sec = random.randint(0, 86399)
    return yyyy_mm_dd.replace(hour=0, minute=0, second=0) + timedelta(seconds=rand_sec)


def compute_due_time_and_duration(lock_days: int):
    """
    依照 tm.getTimestamp() ＋ 鎖倉天數
    回傳 (到期日_整數_day, 實際 duration 秒數)
    """
    curr = tm.getTimestamp()  # Unix 秒
    due_day = (curr + 86_399) // 86_400 + lock_days
    duration_sec = int(due_day * 86_400 - curr)
    return int(due_day), duration_sec

# Test
# dt = datetime(2025, 4, 1, 0, 0, tzinfo=timezone.utc)
# tm.setCurrentTime(dt)
# print(tm.getCurrentTime())
# print(random_time_in_day(dt))
