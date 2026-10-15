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
