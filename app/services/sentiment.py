from datetime import datetime, timezone

from transformers import pipeline

from app.settings import SENTIMENT_BATCH_SIZE, SENTIMENT_REVISION


MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"
BATCH_SIZE = SENTIMENT_BATCH_SIZE


def load_classifier(settings=None):
    import torch
    from huggingface_hub.utils import disable_progress_bars
    from transformers.utils import logging as transformers_logging
    from app.settings import Settings

    disable_progress_bars()
    transformers_logging.disable_progress_bar()
    transformers_logging.disable_default_handler()
    transformers_logging.enable_propagation()
    settings = settings or Settings.from_env()
    torch.set_num_threads(settings.sentiment_threads)
    return pipeline(
        task="text-classification", model=settings.sentiment_model_path,
        revision=settings.sentiment_revision, device="cpu",
    )


def analyze_sentiment(data, classifier=None, revision=SENTIMENT_REVISION):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("input must be a JSON object containing a reviews list")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    reviews = data["reviews"]
    texts = []
    for index, review in enumerate(reviews):
        if not isinstance(review, dict) or not isinstance(review.get("text"), str) or not review["text"].strip():
            raise ValueError(f"review at index {index} must contain nonempty text")
        texts.append(review["text"])
    predictions = []
    if texts:
        if classifier is None:
            classifier = load_classifier()
        predictions = classifier(texts, batch_size=BATCH_SIZE, truncation=True, max_length=512)
    if len(predictions) != len(reviews):
        raise ValueError("classifier returned an unexpected number of predictions")
    analyzed = []
    for review, prediction in zip(reviews, predictions):
        label = prediction["label"].lower()
        score = float(prediction["score"])
        if label not in {"positive", "negative", "neutral"} or not 0 <= score <= 1:
            raise ValueError("classifier returned an invalid sentiment prediction")
        analyzed.append({**review, "sentiment": {"label": label, "score": score}})
    return {
        **data,
        "metadata": {
            **metadata,
            "sentiment_analysis": {
                "model": MODEL,
                "revision": revision,
                "device": "cpu",
                "batch_size": BATCH_SIZE,
                "truncation": True,
                "max_length": 512,
                "analyzed_count": len(analyzed),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        "reviews": analyzed,
    }

