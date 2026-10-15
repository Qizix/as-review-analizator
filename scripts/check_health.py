import argparse
import json
import time
import urllib.error
import urllib.request


def check_health(url, require_gemini=False, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=min(3, max(0.1, deadline - time.monotonic()))) as response:
                data = json.load(response)
            if isinstance(data, dict) and data.get("status") == "ok" and data.get("sentiment_ready") is True:
                if require_gemini and data.get("gemini_configured") is not True:
                    raise RuntimeError("Service is ready but Gemini credentials are not configured")
                return data
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise RuntimeError("Service did not become ready before the health-check deadline")


def main():
    parser = argparse.ArgumentParser(description="Check readiness without making paid API calls")
    parser.add_argument("url")
    parser.add_argument("--require-gemini", action="store_true")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        check_health(args.url, args.require_gemini, args.timeout)
    except RuntimeError as exc:
        parser.exit(1, f"Health check failed: {exc}\n")
    print("Service ready")


if __name__ == "__main__":
    main()
