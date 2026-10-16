from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.collector import validate_inputs


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(pattern=r"^[0-9]+$", min_length=1, max_length=20)
    country: str = "us"
    mode: Literal["fast", "quality"] = "quality"
    review_count: int = Field(default=100, ge=1, le=500, strict=True)
    seed: int = Field(default=42, strict=True, json_schema_extra={"deprecated": True}, description="Accepted for compatibility; ignored because reviews are collected in source order.")

    @field_validator("app_id")
    @classmethod
    def positive_id(cls, value):
        if int(value) == 0:
            raise ValueError("app_id must be positive")
        return value

    @field_validator("country")
    @classmethod
    def country_code(cls, value):
        return validate_inputs("1", value, 1)


class Sentiment(BaseModel):
    label: Literal["positive", "neutral", "negative"]
    score: float | None = Field(default=None, ge=0, le=1)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=10000)
    text: str = Field(min_length=1, max_length=100000)
    rating: int | None = Field(default=None, ge=1, le=5, strict=True)
    sentiment: Sentiment | Literal["positive", "neutral", "negative"] | None = None


class AnalysisResponse(BaseModel):
    app: dict
    collection: dict
    metrics: dict
    insights: dict
    reviews: list[Review]
    metadata: dict
