import math
import threading
import time
from collections import deque
from contextlib import contextmanager

from app.services.execution import AnalysisError


class Admission:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.recent = {kind: deque() for kind in ("analysis", "collection")}
        self.daily = {kind: 0 for kind in self.recent}
        self.day = None

    @contextmanager
    def work(self, kind):
        if not self.settings.work_enabled:
            raise AnalysisError("Review processing is temporarily paused; try again later", 503, "SERVICE_PAUSED", "admission", {"Retry-After": "60"})
        if not self.lock.acquire(blocking=False):
            raise AnalysisError("Another review request is running. Please try again shortly", 409, "ANALYSIS_BUSY", "admission", {"Retry-After": "5"})
        try:
            minute_limit, daily_limit = {
                "analysis": (self.settings.analyze_requests_per_minute, self.settings.analyze_requests_per_day),
                "collection": (self.settings.collect_requests_per_minute, self.settings.collect_requests_per_day),
            }[kind]
            now = time.monotonic()
            recent = self.recent[kind]
            while recent and recent[0] <= now - 60:
                recent.popleft()
            if len(recent) >= minute_limit:
                retry = max(1, math.ceil(60 - (now - recent[0])))
                raise AnalysisError("Request rate limit reached; try again later", 429, "RATE_LIMITED", "admission", {"Retry-After": str(retry)})
            wall_time = time.time()
            day = int(wall_time // 86400)
            if self.day != day:
                self.day = day
                self.daily = {kind: 0 for kind in self.daily}
            if self.daily[kind] >= daily_limit:
                retry = max(1, math.ceil(86400 - wall_time % 86400))
                raise AnalysisError("This demo's daily request allowance is exhausted; please return tomorrow", 429, "DAILY_LIMITED", "admission", {"Retry-After": str(retry)})
            recent.append(now)
            self.daily[kind] += 1
            yield
        finally:
            self.lock.release()
