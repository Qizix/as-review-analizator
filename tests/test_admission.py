import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.admission import Admission
from app.main import create_app
from app.services.execution import AnalysisError
from app.settings import Settings


@pytest.mark.parametrize("active,contender", [
    ("/api/analyze", "/api/analyze"),
    ("/api/analyze", "/api/reviews/collect"),
    ("/api/reviews/collect", "/api/analyze"),
    ("/api/reviews/collect", "/api/reviews/collect"),
])
def test_one_heavy_request_and_responsive_health(monkeypatch, active, contender):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    app = create_app(Settings(otel_enabled=False), lambda _: Mock())
    entered, release = threading.Event(), threading.Event()
    report = {"app": {}, "collection": {}, "metrics": {}, "insights": {}, "reviews": [], "metadata": {}}

    def work(*args):
        entered.set()
        assert release.wait(5)
        return report

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(app.state.analyzer, "_analyze", work)
        monkeypatch.setattr(app.state.analyzer, "_collect", work)
        first = pool.submit(client.post, active, json={"app_id": "123"})
        try:
            assert entered.wait(5)
            assert client.get("/api/health").status_code == 200
            assert client.get("/").status_code == 200
            blocked = client.post(contender, json={"app_id": "123"})
            assert blocked.status_code == 409
            assert blocked.headers["Retry-After"] == "5"
            assert set(blocked.json()) == {"detail"}
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        assert client.post(contender, json={"app_id": "123"}).status_code == 200


def test_busy_attempts_do_not_spend_allowance_and_failure_releases_gate():
    admission = Admission(Settings(analyze_requests_per_minute=1))
    with admission.work("collection"):
        with pytest.raises(AnalysisError) as busy:
            with admission.work("analysis"):
                pytest.fail("Concurrent work admitted")
        assert busy.value.status == 409
    with pytest.raises(RuntimeError):
        with admission.work("analysis"):
            raise RuntimeError("Provider failed")
    with admission.work("collection"):
        pass
    with pytest.raises(AnalysisError) as limited:
        with admission.work("analysis"):
            pytest.fail("Rate limit bypassed")
    assert limited.value.status == 429


@pytest.mark.parametrize("kind", ["analysis", "collection"])
def test_minute_daily_limits_and_utc_reset(monkeypatch, kind):
    clock = [100.0]
    wall = [86400.0 + 100]
    monkeypatch.setattr("app.admission.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.admission.time.time", lambda: wall[0])
    admission = Admission(Settings(analyze_requests_per_minute=1, collect_requests_per_minute=1, analyze_requests_per_day=1, collect_requests_per_day=1))
    with admission.work(kind):
        pass
    with pytest.raises(AnalysisError) as minute:
        with admission.work(kind):
            pytest.fail("Minute limit bypassed")
    assert minute.value.headers == {"Retry-After": "60"}
    clock[0] += 61
    with pytest.raises(AnalysisError) as daily:
        with admission.work(kind):
            pytest.fail("Daily limit bypassed")
    assert daily.value.code == "DAILY_LIMITED"
    assert int(daily.value.headers["Retry-After"]) > 0
    wall[0] += 86400
    with admission.work(kind):
        pass


def test_pause_blocks_work_but_keeps_dashboard_and_health(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    with TestClient(create_app(Settings(work_enabled=False, otel_enabled=False), lambda _: Mock())) as client:
        for path in ("/api/analyze", "/api/reviews/collect"):
            response = client.post(path, json={"app_id": "123"})
            assert response.status_code == 503
            assert response.headers["Retry-After"] == "60"
        assert client.get("/api/health").status_code == 200
        assert client.get("/").status_code == 200
