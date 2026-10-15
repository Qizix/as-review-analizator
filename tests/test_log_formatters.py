import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.log_formatters import ConsoleFormatter, JsonFormatter
from app.main import create_app
from app.settings import Settings


SECRET = "PRIVATE_EXCEPTION_OR_REVIEW_CONTENT"


@pytest.mark.parametrize("event", ["application.failed", "telemetry.initialization_failed", "telemetry.export_failed"])
def test_process_failure_retains_safe_diagnostics_in_both_formats(event):
    record = logging.LogRecord("review_analyzer", logging.ERROR, __file__, 1, SECRET, (), (RuntimeError, RuntimeError(SECRET), None))
    record.event_data = {
        "event": event, "message": event.replace(".", " "), "error_type": "RuntimeError",
        "error": {"type": "RuntimeError", "frames": [{"file": "model_loader.py", "line": 42, "function": "load_model"}]},
    }
    console = ConsoleFormatter().format(record)
    structured = json.loads(JsonFormatter().format(record))
    assert "RuntimeError" in console
    assert "model_loader.py:42 in load_model" in console
    assert SECRET not in console
    assert structured["error_type"] == "RuntimeError"
    assert structured["error"]["frames"][0]["function"] == "load_model"
    assert "\n" not in JsonFormatter().format(record)
    assert SECRET not in JsonFormatter().format(record)
    assert "severity" not in record.event_data


def test_dependency_exception_text_is_never_rendered():
    record = logging.LogRecord("google", logging.ERROR, __file__, 1, SECRET, (), (RuntimeError, RuntimeError(SECRET), None))
    assert SECRET not in ConsoleFormatter().format(record)
    assert SECRET not in JsonFormatter().format(record)


def test_console_startup_failure_preserves_internal_error_type(capsys):
    def factory(settings):
        raise RuntimeError(SECRET)

    with pytest.raises(RuntimeError, match=SECRET):
        with TestClient(create_app(Settings(log_format="console", otel_enabled=False), classifier_factory=factory)):
            pass
    output = capsys.readouterr().out
    assert "application failed" in output
    assert "RuntimeError" in output
    assert "lifespan" in output
    assert SECRET not in output
