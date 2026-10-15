import json
import logging
from collections import Counter
from datetime import datetime, timezone


def prepare_record(record):
    data = dict(getattr(record, "event_data", {}))
    data.setdefault("event", "dependency.diagnostic")
    data.setdefault("message", "Dependency diagnostic; raw message withheld")
    data.update(severity=record.levelname, timestamp=datetime.fromtimestamp(record.created, timezone.utc).isoformat())
    data.setdefault("logger", record.name)
    return data


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(prepare_record(record), ensure_ascii=True, allow_nan=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        data = prepare_record(record)
        if data["event"] == "trace.span":
            return json.dumps(data)
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        title = f'{timestamp}  {data["severity"]:7}  {data["message"]}'
        lines = [title]
        if data["event"].startswith("request."):
            lines.append(f'  Request  {data["request_id"]}')
            if data.get("trace_id"):
                lines.append(f'  Trace    {data["trace_id"]}')
            if data.get("app_id"):
                lines.append(f'  App      {data["app_id"]} / {data.get("country", "?")}')
            if "reviews_collected" in data:
                lines.append(f'  Reviews  {data["reviews_collected"]}/{data.get("requested_count", "?")} · issues {data.get("issues_identified", "—")} · shortfall {data.get("shortfall", 0)}')
            stages = {}
            for stage in data.get("stages", []):
                entry = stages.setdefault(stage["name"], {"duration_ms": 0, "count": 0, "failed": 0})
                entry["duration_ms"] += stage["duration_ms"]
                entry["count"] += 1
                entry["failed"] += stage["outcome"] == "error"
            for name, stage in stages.items():
                repeated = f' ×{stage["count"]}' if stage["count"] > 1 else ""
                failed = f' · {stage["failed"]} failed' if stage["failed"] else ""
                lines.append(f'  {name + repeated:28} {stage["duration_ms"]:9.1f} ms{failed}')
            if "gemini_usage_available" in data:
                usage = data.get("gemini_usage", {})
                counts = " · ".join(f'{key.removesuffix("_token_count")}: {value}' for key, value in usage.items())
                lines.append(f'  Tokens   {counts or "unavailable"}')
                if not data["gemini_usage_available"] and counts:
                    lines.append("  Warning  Token usage is incomplete")
            for code, count in Counter(item["code"] for item in data.get("warnings", [])).items():
                lines.append(f'  Warning  {code}' + (f" ×{count}" if count > 1 else ""))
        if data.get("error_type") or data.get("error"):
            error = data.get("error", {})
            stage = data.get("error_stage", error.get("stage"))
            error_type = data.get("error_type", error.get("type", "unknown"))
            detail = f"{stage}: {error_type}" if stage else error_type
            if error.get("type") and error["type"] != error_type:
                detail += f' ({error["type"]})'
            lines.append(f"  Error    {detail}")
            for frame in error.get("frames", [])[-3:]:
                lines.append(f'           {frame["file"]}:{frame["line"]} in {frame["function"]}')
        if data.get("omitted_detail_count"):
            lines.append(f'  Details  {data["omitted_detail_count"]} omitted')
        return "\n".join(lines) + ("\n" if len(lines) > 1 else "")
