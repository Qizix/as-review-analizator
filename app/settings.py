import os
from pathlib import Path

from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
SENTIMENT_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
SENTIMENT_REVISION = "3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7"
GEMINI_MODEL = "gemini-3.5-flash-lite"


class Settings(BaseModel):
    analysis_timeout_seconds: int = Field(default=280, ge=30, le=290)
    analyze_requests_per_minute: int = Field(default=3, ge=1, le=60)
    sentiment_threads: int = Field(default=1, ge=1, le=8)
    sentiment_model_path: str = SENTIMENT_MODEL
    sentiment_revision: str = Field(default=SENTIMENT_REVISION, pattern=r"^[a-f0-9]{40}$")
    max_review_count: int = Field(default=100, ge=1, le=500)
    gemini_model: str = GEMINI_MODEL

    @classmethod
    def from_env(cls):
        return cls.model_validate({key: os.environ[key.upper()] for key in cls.model_fields if key.upper() in os.environ})
