import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.metrics import NoOpMeterProvider
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import RequestObservationMiddleware, configure_logging, emit, error_details, observe, record_exception
from app.schemas import AnalysisResponse, AnalyzeRequest
from app.services.analyzer import Analyzer
from app.services.execution import AnalysisError
from app.services.collector import COUNTRIES
from app.services.sentiment import load_classifier
from app.settings import ROOT, Settings
from app.telemetry import Telemetry, diagnostic


def create_app(settings=None, classifier_factory=load_classifier, span_exporter=None):
    settings = settings or Settings.from_env()
    configure_logging(settings)
    telemetry = Telemetry(settings, span_exporter)

    @asynccontextmanager
    async def lifespan(app):
        telemetry.start()
        started = time.monotonic()
        try:
            emit("model.loading", model=settings.sentiment_model_path, device="cpu")
            app.state.analyzer = Analyzer(settings, classifier_factory(settings))
            emit("application.ready", sentiment_revision=settings.sentiment_revision, model_load_duration_ms=round((time.monotonic() - started) * 1000, 3), collection_source="apple_catalog", tracing_enabled=telemetry.enabled, trace_export_ready=telemetry.export_ready)
            yield
        except Exception as exc:
            emit("application.failed", logging.ERROR, error=error_details(exc))
            raise
        finally:
            app.state.analyzer = None
            telemetry.shutdown()
            emit("application.shutdown")

    app = FastAPI(title="App Store Review Analyzer", version="1.0.0", lifespan=lifespan)
    app.state.telemetry = telemetry
    app.add_middleware(RequestObservationMiddleware, telemetry=telemetry, settings=settings)
    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
    templates = Jinja2Templates(directory=ROOT / "frontend")

    @app.exception_handler(AnalysisError)
    async def analysis_error(request, exc):
        observe(error_stage=exc.stage, error_type=exc.code)
        if exc.status >= 500:
            record_exception(trace.get_current_span(), exc)
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)}, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        code = {429: "RATE_LIMITED", 503: "GEMINI_NOT_CONFIGURED"}.get(exc.status_code, "HTTP_ERROR")
        observe(error_type=code, error_stage="http.request")
        if exc.status_code >= 500:
            record_exception(trace.get_current_span(), exc)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        observe(error_type="INVALID_REQUEST", error_stage="http.validation")
        return await request_validation_exception_handler(request, exc)

    @app.get("/", include_in_schema=False)
    def dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"max_review_count": settings.max_review_count, "countries": sorted(COUNTRIES)})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "sentiment_ready": app.state.analyzer is not None, "gemini_configured": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))}

    @app.post("/api/analyze", response_model=AnalysisResponse)
    def analyze(payload: AnalyzeRequest):
        observe(app_id=payload.app_id, country=payload.country, requested_count=payload.review_count)
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            raise HTTPException(503, "Gemini credentials are not configured on the server")
        return app.state.analyzer.analyze(payload)

    @app.post("/api/reviews/collect")
    def collect(payload: AnalyzeRequest):
        return app.state.analyzer.collect(payload, time.monotonic() + settings.analysis_timeout_seconds)

    if telemetry.enabled:
        try:
            FastAPIInstrumentor.instrument_app(
                app, tracer_provider=telemetry.provider, meter_provider=NoOpMeterProvider(),
                excluded_urls=r"/api/health(?:\?.*)?$", exclude_spans=["receive", "send"],
                http_capture_headers_server_request=[], http_capture_headers_server_response=[],
            )
        except Exception as exc:
            diagnostic("telemetry.instrumentation_failed", exc)
    return app


app = create_app()
