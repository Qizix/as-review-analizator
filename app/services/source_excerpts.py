def source_excerpts(review):
    excerpts = []
    for field in ("title", "text", "text_tail"):
        text = review.get(field, "")
        start = 0
        while start < len(text):
            end = min(start + 240, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + 120, end)
                if boundary != -1:
                    end = boundary + 1
            excerpt = text[start:end]
            if excerpt.strip():
                excerpts.append(excerpt)
            start = end
    return excerpts


def reference_excerpts(reviews):
    payload, sources = [], {}
    for review in reviews:
        excerpts = []
        for text in source_excerpts(review):
            identifier = len(sources)
            sources[identifier] = (review["id"], text)
            excerpts.append({"id": identifier, "text": text})
        payload.append({"id": review["id"], "excerpts": excerpts})
    return payload, sources
