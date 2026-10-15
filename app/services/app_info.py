import httpx


def lookup_app_info(app_id, country):
    app = {"app_id": app_id, "country": country}
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            response = client.get("https://itunes.apple.com/lookup", params={"id": app_id, "country": country})
            response.raise_for_status()
            results = response.json().get("results", [])
        if results and str(results[0].get("trackId")) == app_id:
            item = results[0]
            app.update({"name": item.get("trackName"), "developer": item.get("artistName"), "store_url": item.get("trackViewUrl"), "icon_url": item.get("artworkUrl100"), "version": item.get("version")})
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        pass
    return app
