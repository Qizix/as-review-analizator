# Negative-review phrase ranking

`scripts/extract_phrases.py` reads annotated review JSON from `analyze_sentiment.py` and ranks phrase candidates by the number of negative reviews mentioning them. It uses scikit-learn's [CountVectorizer](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.CountVectorizer.html), installed and locked through uv.

## Usage and input

```bash
uv run scripts/extract_phrases.py output/reviews_sentiment.json
uv run scripts/extract_phrases.py output/reviews_sentiment.json --output output/phrases.json
```

Input is required. Output defaults to `INPUT_STEM_phrases.json` alongside the input; parent directories are created and existing output is overwritten. The input and output paths must differ.

The JSON must contain a `reviews` list and optional object-valued `metadata`. Each review must have nonempty string `text` and a `sentiment.label` of `positive`, `negative`, or `neutral`, as produced by the sentiment script. Missing or invalid labels fail rather than treating unclassified reviews as nonnegative. Only negative review texts enter the vectorizer. Titles and star ratings are unused. Each negative review record is one document; the collector already guarantees unique review IDs.

## Extraction and ranking

The vectorizer uses `ngram_range=(1, 3)`, `binary=True`, `min_df=1`, `lowercase=True`, and `stop_words=None`. This retains rare candidates and preserves word order while extracting one-, two-, and three-token sequences. Summing each column of the binary sparse matrix yields document frequency, so repeated occurrences within one review count once. The full document-term matrix remains sparse.

`token_pattern` is `(?u)\b\w\w+(?:['’]\w+)?\b`. It follows the default two-character minimum while retaining apostrophe contractions such as `don't`, `can't`, and `isn’t` as individual tokens. Lowercasing is applied by CountVectorizer. Original review text is unchanged; punctuation otherwise separates tokens, and n-grams can cross punctuation boundaries. Single-character words and punctuation-only/emoji-only text contribute no tokens.

Generic candidates are removed only after extraction and frequency calculation. `GENERIC_WORDS` combines scikit-learn's `ENGLISH_STOP_WORDS`, `app`, `apps`, `application`, `applications`, filler words (`just`, `really`, `actually`, `literally`, `basically`, `please`, `thanks`, `thank`, `hello`, `hi`), and a small set of common nonnegative English contractions. A candidate is filtered if every token is generic, unless any token is a protected negation or ends in `n't`. Curly apostrophes are normalized only when testing this filter, not in output phrases. `NEGATIONS` protects `no`, `not`, `never`, `neither`, `nor`, `cannot`, `without`, `nothing`, `nowhere`, `nobody`, `none`, `hardly`, and `barely`.

For example, `the app` is removed, while `is not working`, `never works`, and `don't like` remain intact. Conservative protection also retains negation unigrams and generic fragments such as `not the`. This is an English heuristic, not a classifier of actionable complaints. Candidates containing at least one nongeneric token remain; platform names and other domain-specific noise may therefore remain. No stemming, merging of overlapping phrases, phrase rewriting, minimum frequency threshold beyond 1, or top-N truncation is applied.

All retained candidates are sorted by descending document frequency and then ascending phrase text for deterministic ties. A phrase's `negative_review_fraction` is document frequency divided by the number of negative reviews, including negative documents without tokenizable text. An input with no negative reviews, no tokenizable words, or only filtered candidates saves an empty phrase list successfully.

## Output and verification

Output contains `metadata` and `phrases`, with no copy of the raw reviews. Collection and sentiment metadata are preserved. `metadata.phrase_extraction` includes input and negative review counts, raw candidate count, removed/returned counts, vectorizer settings, filtering policy, and a UTC extraction timestamp. Each phrase contains `phrase`, integer `document_frequency`, and `negative_review_fraction` between 0 and 1. Counts overlap across phrases, so fractions do not sum to 1.

Successful writes return status 0. File, JSON, or input-validation errors report to stderr and return status 1. Argument errors return status 2. Input files remain unchanged.

Run `uv run python -m unittest discover -s tests -v`. Tests check review-level counting despite repeated occurrences, negative-only selection, rare candidate retention, n-gram structure, generic filtering, preserved negations and contractions, deterministic ranking, empty/tokenless inputs, invalid labels, metadata preservation, and CLI output behavior. The script is also run against the saved sentiment results, with frequencies independently checked against the set of tokenized phrases in each negative review.
