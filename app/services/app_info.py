import httpx
from opentelemetry import trace

from app.observability import record_exception, warning


def lookup_app_info(app_id, country):
    app = {"app_id": app_id, "country": country}
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            response = client.get("https://itunes.apple.com/lookup", params={"id": app_id, "country": country})
            trace.get_current_span().set_attribute("http.response.status_code", response.status_code)
            response.raise_for_status()
            results = response.json().get("results", [])
        if results and str(results[0].get("trackId")) == app_id:
            item = results[0]
            app.update({"name": item.get("trackName"), "developer": item.get("artistName"), "store_url": item.get("trackViewUrl"), "icon_url": item.get("artworkUrl100"), "version": item.get("version")})
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        record_exception(trace.get_current_span(), exc)
        warning("APP_INFO_UNAVAILABLE", error_type=type(exc).__name__)
    return app
