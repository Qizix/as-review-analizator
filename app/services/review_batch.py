from copy import deepcopy

from app.services.preprocessor import group_reviews, preprocess_collection


class ReviewBatch:
    def __init__(self, data):
        self._source = deepcopy(data)
        self._prepared = preprocess_collection(self._source)

    @property
    def source(self):
        return deepcopy(self._source)

    @property
    def groups(self):
        return deepcopy(group_reviews(self._prepared))

    @property
    def preprocessing(self):
        return deepcopy(self._prepared["metadata"]["preprocessing"])

    @property
    def semantic_preprocessing(self):
        return {
            "emoji": "preserved", "whitespace": "preserved", "title_limit": 200,
            "text_limit": 4000, "oversized_text": "separate 3000-character head and 1000-character tail",
            "truncated_review_ids": [review["id"] for review in self._source["reviews"] if len(review["title"]) > 200 or len(review["text"]) > 4000],
        }

    @property
    def semantic_reviews(self):
        result = []
        for review in self._source["reviews"]:
            item = {"id": review["id"], "title": review["title"][:200], "text": review["text"][:4000]}
            if len(review["text"]) > 4000:
                item["text"] = review["text"][:3000]
                item["text_tail"] = review["text"][-1000:]
            result.append(item)
        return result

    def attach_sentiments(self, predictions):
        expected = {review["id"] for review in self._source["reviews"]}
        labels = {item.review_id: item.sentiment for item in predictions}
        if len(labels) != len(predictions) or set(labels) != expected:
            from app.services.validator import InsightValidationError

            raise InsightValidationError("sentiment_ids_incomplete_duplicate_or_unknown")
        names = {-1: "negative", 0: "neutral", 1: "positive"}
        for data in (self._source, self._prepared):
            for review in data["reviews"]:
                review["sentiment"] = {"label": names[labels[review["id"]]]}

    def classify(self, classifier, revision):
        from app.services.sentiment import analyze_sentiment

        classified = analyze_sentiment(self._prepared, classifier, revision)
        source = {
            **classified,
            "reviews": [
                {**original, "sentiment": prepared["sentiment"]}
                for original, prepared in zip(self._source["reviews"], classified["reviews"], strict=True)
            ],
        }
        self._prepared = classified
        self._source = source
