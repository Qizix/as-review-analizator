from collections import Counter


SENTIMENTS = ("positive", "neutral", "negative")
PRIORITIES = ("high", "medium", "low")


def calculate_metrics(data, insights):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("reviews input must be an object containing a reviews list")
    if not isinstance(insights, dict) or not isinstance(insights.get("insights"), dict):
        raise ValueError("insights input must be a report produced by generate_insights.py")
    for source in (data, insights):
        if not isinstance(source.get("metadata", {}), dict):
            raise ValueError("metadata must be an object")
    for key in ("app_id", "country"):
        first, second = data.get("metadata", {}).get(key), insights.get("metadata", {}).get(key)
        if first is not None and second is not None and first != second:
            raise ValueError(f"reviews and insights have different {key} values")
    ratings = Counter({str(star): 0 for star in range(1, 6)})
    sentiments = Counter({label: 0 for label in SENTIMENTS})
    review_ids = set()
    negative_ids = set()
    classified_ids = set()
    for index, review in enumerate(data["reviews"]):
        if not isinstance(review, dict):
            raise ValueError(f"review at index {index} must be an object")
        review_id = review.get("id")
        if not isinstance(review_id, str) or not review_id.strip() or review_id in review_ids:
            raise ValueError(f"review at index {index} must have a unique nonempty string ID")
        review_ids.add(review_id)
        rating = review.get("rating")
        if rating is not None:
            if type(rating) is not int or not 1 <= rating <= 5:
                raise ValueError(f"review {review_id} has an invalid rating")
            ratings[str(rating)] += 1
        sentiment = review.get("sentiment")
        label = sentiment.get("label") if isinstance(sentiment, dict) else sentiment
        if label is not None:
            if label not in SENTIMENTS:
                raise ValueError(f"review {review_id} has an invalid sentiment")
            classified_ids.add(review_id)
            sentiments[label] += 1
            if label == "negative":
                negative_ids.add(review_id)
    issues = []
    covered_ids = set()
    categories = set()
    for priority in PRIORITIES:
        buckets = insights["insights"].get(priority)
        if not isinstance(buckets, dict):
            raise ValueError(f"insights must contain {priority} priority buckets")
        for recurrence in ("repeated", "single"):
            items = buckets.get(recurrence)
            if not isinstance(items, list):
                raise ValueError(f"{priority}.{recurrence} must be a list")
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("category"), str) or not item["category"].strip():
                    raise ValueError("each issue must have a nonempty category")
                category = " ".join(item["category"].split())
                if category.casefold() in categories:
                    raise ValueError("issue categories must be unique")
                categories.add(category.casefold())
                evidence = item.get("evidence_ids")
                if not isinstance(evidence, list) or not evidence or not all(isinstance(value, str) for value in evidence):
                    raise ValueError("each issue must have a nonempty list of review IDs")
                ids = set(evidence)
                if not ids <= classified_ids:
                    raise ValueError("issue evidence must reference classified reviews in the reviews input")
                covered_ids.update(ids & negative_ids)
                issues.append({
                    "category": category,
                    "priority": priority,
                    "frequency": len(ids),
                    "negative_review_count": len(ids & negative_ids),
                    "negative_review_fraction": len(ids & negative_ids) / len(negative_ids) if negative_ids else None,
                    "evidence_ids": sorted(ids),
                })
    issues.sort(key=lambda item: (PRIORITIES.index(item["priority"]), -item["frequency"], item["category"].casefold()))
    total = len(review_ids)
    rated = sum(ratings.values())
    classified = sum(sentiments.values())
    coverage = len(covered_ids) / len(negative_ids) if negative_ids else None
    return {
        "metadata": data.get("metadata", {}).copy(),
        "total_reviews": total,
        "ratings": {
            "rated_reviews": rated,
            "unrated_reviews": total - rated,
            "average": sum(int(star) * count for star, count in ratings.items()) / rated if rated else None,
            "counts": dict(ratings),
            "percentages": {star: count / rated * 100 if rated else None for star, count in ratings.items()},
        },
        "sentiments": {
            "classified_reviews": classified,
            "unclassified_reviews": total - classified,
            "counts": dict(sentiments),
            "percentages": {label: count / classified * 100 if classified else None for label, count in sentiments.items()},
        },
        "issues": {
            "negative_reviews": len(negative_ids),
            "covered_negative_reviews": len(covered_ids),
            "uncovered_negative_reviews": len(negative_ids - covered_ids),
            "coverage": coverage,
            "coverage_percentage": coverage * 100 if coverage is not None else None,
            "items": issues,
        },
    }

