import time
from contextlib import contextmanager, nullcontext

from app.observability import operation


class AnalysisError(Exception):
    def __init__(self, message, status=502, code="ANALYSIS_FAILED", stage=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.stage = stage


class AnalysisRun:
    def __init__(self, settings):
        self.settings = settings
        self.started = time.monotonic()
        self.active_stage = "startup"
        self.durations = {}
        self.usage = {}

    @property
    def deadline(self):
        return self.started + self.settings.analysis_timeout_seconds

    def check(self):
        if time.monotonic() >= self.deadline:
            raise AnalysisError("Analysis exceeded its processing deadline", 504, "ANALYSIS_TIMEOUT", self.active_stage)

    @contextmanager
    def stage(self, name, span_name=None, attributes=None):
        self.active_stage = name
        self.check()
        started = time.monotonic()
        with operation(span_name, attributes) if span_name else nullcontext() as span:
            try:
                yield span
            finally:
                self.durations[name] = round(self.durations.get(name, 0) + time.monotonic() - started, 4)
