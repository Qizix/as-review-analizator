FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY NOTICE ./NOTICE
COPY app ./app
COPY prompts ./prompts
COPY scripts/download_model.py ./scripts/download_model.py
RUN .venv/bin/python -m scripts.download_model --output /app/models/sentiment
COPY frontend ./frontend
RUN useradd --create-home --uid 10001 analyzer
USER analyzer
ENV SENTIMENT_MODEL_PATH=/app/models/sentiment HF_HUB_OFFLINE=1
EXPOSE 8080
CMD ["sh", "-c", "exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --limit-concurrency 16"]
