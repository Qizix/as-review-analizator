import emoji

SENTIMENTS = ("negative", "positive", "neutral")


def preprocess_collection(data):
    reviews = []
    truncated_ids = []
    for review in data["reviews"]:
        title, title_cut = preprocess_text(review["title"], 200)
        text, text_cut = preprocess_text(review["text"], 4000)
        reviews.append({**review, "title": title, "text": text})
        truncated = [name for name, cut in (("title", title_cut), ("text", text_cut)) if cut]
        if truncated:
            reviews[-1]["truncated_fields"] = truncated
            truncated_ids.append(review["id"])
    return {
        **data, "reviews": reviews,
        "metadata": {**data["metadata"], "preprocessing": {"emoji": "descriptive words", "text_limit": 4000, "title_limit": 200, "truncated_review_ids": truncated_ids}},
    }


def preprocess_text(text, limit):
    text = emoji.replace_emoji(
        text, replace=lambda value, _: "[" + emoji.demojize(value).strip(":").replace("_", " ") + "]",
    )
    text = " ".join(text.split())
    if len(text) <= limit:
        return text, False
    marker = " [truncated] "
    head = limit * 3 // 4
    tail = limit - head - len(marker)
    return text[:head] + marker + text[-tail:], True


def group_reviews(data):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("input must be a JSON object containing a reviews list")
    if not isinstance(data.get("metadata", {}), dict):
        raise ValueError("metadata must be a JSON object")
    groups = {sentiment: [] for sentiment in SENTIMENTS}
    seen = set()
    for index, review in enumerate(data["reviews"]):
        if not isinstance(review, dict):
            raise ValueError(f"review at index {index} must be an object")
        review_id = review.get("id")
        if not isinstance(review_id, str) or not review_id.strip() or review_id in seen:
            raise ValueError(f"review at index {index} must have a unique nonempty string ID")
        title, text, rating = review.get("title"), review.get("text"), review.get("rating")
        if not isinstance(title, str) or not isinstance(text, str) or not text.strip():
            raise ValueError(f"review {review_id} must have a string title and nonempty text")
        if type(rating) is not int or not 1 <= rating <= 5:
            raise ValueError(f"review {review_id} must have an integer rating from 1 to 5")
        sentiment = review.get("sentiment")
        label = sentiment.get("label") if isinstance(sentiment, dict) else sentiment
        if label not in SENTIMENTS:
            raise ValueError(f"review {review_id} must have a valid sentiment; run analyze_sentiment.py first")
        seen.add(review_id)
        prepared = {"id": review_id, "title": title, "text": text, "rating": rating}
        if review.get("truncated_fields"):
            prepared["truncated_fields"] = review["truncated_fields"]
        groups[label].append(prepared)
    return groups



def prepare_reviews(data):
    group_reviews(data)
    return group_reviews(preprocess_collection({**data, "metadata": data.get("metadata", {})}))
