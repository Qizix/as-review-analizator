import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.check_health import check_health


def response(**overrides):
    return io.BytesIO(json.dumps({"status": "ok", "sentiment_ready": True, "gemini_configured": False, **overrides}).encode())


def test_health_check_waits_for_readiness_without_analyzing():
    with patch("scripts.check_health.urllib.request.urlopen", side_effect=[response(sentiment_ready=False), response(gemini_configured=True)]) as request, patch("scripts.check_health.time.sleep"):
        assert check_health("https://example.test/", require_gemini=True)["sentiment_ready"]
    assert request.call_count == 2
    assert all(call.args == ("https://example.test/api/health",) for call in request.call_args_list)


def test_health_check_rejects_missing_credentials():
    with patch("scripts.check_health.urllib.request.urlopen", return_value=response()):
        with pytest.raises(RuntimeError, match="credentials are not configured"):
            check_health("https://example.test", require_gemini=True)


def test_health_check_retries_malformed_data_then_times_out():
    with patch("scripts.check_health.time.monotonic", side_effect=[0, 0, 0, 1, 2]), patch("scripts.check_health.time.sleep"), patch("scripts.check_health.urllib.request.urlopen", return_value=io.BytesIO(b"invalid json")):
        with pytest.raises(RuntimeError, match="deadline"):
            check_health("https://example.test", timeout=1)


@pytest.mark.parametrize("failure", [None, "dirty", "tests"])
def test_deploy_preflight_and_public_single_instance_flags(tmp_path, failure):
    commands = tmp_path / "commands"
    commands.mkdir()
    calls = tmp_path / "calls.jsonl"
    for command in ("git", "uv", "npm", "gcloud"):
        executable = commands / command
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "command = Path(sys.argv[0]).name\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['DEPLOY_TEST_CALLS'], 'a') as stream:\n"
            "    stream.write(json.dumps([command, *args]) + '\\n')\n"
            "if command == 'git':\n"
            "    if args == ['status', '--porcelain'] and os.environ['DEPLOY_TEST_FAILURE'] == 'dirty': print(' M app/main.py')\n"
            "    if args == ['rev-parse', 'HEAD']: print('abc123')\n"
            "if command == 'uv' and 'pytest' in args and os.environ['DEPLOY_TEST_FAILURE'] == 'tests': sys.exit(1)\n"
            "if command == 'gcloud' and 'describe' in args: print('https://example.test')\n"
        )
        executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "infra/deploy.sh", "test-project", "test-region", "test-account"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        env={**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"], "DEPLOY_TEST_CALLS": str(calls), "DEPLOY_TEST_FAILURE": failure or ""},
    )
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    cloud_calls = [call for call in recorded if call[0] == "gcloud"]
    if failure:
        assert result.returncode != 0
        assert not cloud_calls
        return
    assert result.returncode == 0, result.stderr
    deploy = next(call for call in cloud_calls if call[1:3] == ["run", "deploy"])
    assert "--no-invoker-iam-check" in deploy
    for flag, value in (("--max", "1"), ("--max-instances", "1"), ("--concurrency", "8")):
        assert deploy[deploy.index(flag) + 1] == value
    assert deploy[deploy.index("--image") + 1].endswith(":abc123")
    assert any("--to-latest" in call for call in cloud_calls)
    assert recorded[-1][-1] == "--require-gemini"
