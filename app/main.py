import json
import logging
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.schemas import AnalysisResponse, AnalyzeRequest, MetricsRequest
from app.services.analyzer import AnalysisError, Analyzer, empty_insights
from app.services.metrics import calculate_metrics
from app.services.sentiment import load_classifier
from app.settings import ROOT, Settings


logger = logging.getLogger("review_analyzer")
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger.setLevel(logging.INFO)


def normalize_insights(value, metadata):
    if "insights" in value:
        return value
    result = empty_insights(metadata)
    if not isinstance(value.get("issues"), list):
        raise ValueError("insights must contain an issues list")
    for issue in value["issues"]:
        if not isinstance(issue, dict) or not isinstance(issue.get("evidence_ids"), list) or not all(isinstance(review_id, str) for review_id in issue["evidence_ids"]):
            raise ValueError("each issue must contain a list of string evidence IDs")
        priority = issue.get("priority")
        ids = issue.get("evidence_ids", [])
        if priority not in result["insights"]:
            raise ValueError("issue priority must be high, medium, or low")
        result["insights"][priority]["repeated" if len(set(ids)) > 1 else "single"].append(issue)
    return result


def create_app(settings=None, classifier_factory=load_classifier):
    settings = settings or Settings.from_env()
    requests = deque()
    rate_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.analyzer = Analyzer(settings, classifier_factory(settings))
        logger.info(json.dumps({"event": "application_ready", "sentiment_revision": settings.sentiment_revision}))
        yield
        app.state.analyzer = None

    app = FastAPI(title="App Store Review Analyzer", version="1.0.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
    templates = Jinja2Templates(directory=ROOT / "frontend")

    @app.exception_handler(AnalysisError)
    async def analysis_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    @app.middleware("http")
    async def request_log(request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        logger.info(json.dumps({"event": "http_request", "method": request.method, "path": request.url.path, "status": response.status_code, "duration_seconds": round(time.monotonic() - started, 4)}))
        return response

    @app.get("/", include_in_schema=False)
    def dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"max_review_count": settings.max_review_count})

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "sentiment_ready": app.state.analyzer is not None, "gemini_configured": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}

    @app.post("/api/v1/analyze", response_model=AnalysisResponse)
    def analyze(payload: AnalyzeRequest):
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            raise HTTPException(503, "Gemini credentials are not configured on the server")
        with rate_lock:
            now = time.monotonic()
            while requests and requests[0] <= now - 60:
                requests.popleft()
            if len(requests) >= settings.analyze_requests_per_minute:
                raise HTTPException(429, "Analysis rate limit reached; retry in one minute", headers={"Retry-After": "60"})
            requests.append(now)
        return app.state.analyzer.analyze(payload)

    @app.post("/api/v1/reviews/collect")
    def collect(payload: AnalyzeRequest):
        return app.state.analyzer.collect(payload, time.monotonic() + settings.analysis_timeout_seconds)

    @app.post("/api/v1/metrics")
    def metrics(payload: MetricsRequest):
        data = {"metadata": payload.metadata, "reviews": [review.model_dump() for review in payload.reviews]}
        try:
            grouping = normalize_insights(payload.insights, payload.metadata) if payload.insights is not None else empty_insights(payload.metadata)
            result = calculate_metrics(data, grouping)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"total_reviews": result["total_reviews"], "ratings": result["ratings"], "sentiment": result["sentiments"], "issues": result["issues"] if payload.insights is not None else None}

    return app


app = create_app()
