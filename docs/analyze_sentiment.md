# Sentiment classification

`scripts/analyze_sentiment.py` reads a JSON file produced by `fetch_reviews.py` and classifies each review's `text` using the Transformers text-classification pipeline with `cardiffnlp/twitter-roberta-base-sentiment-latest`. Dependencies are managed by uv in `pyproject.toml` and `uv.lock`.

## Usage

```bash
uv run scripts/analyze_sentiment.py output/reviews.json
uv run scripts/analyze_sentiment.py output/reviews.json --output output/sentiment.json
```

The input is a required positional path. Output defaults to a sibling file named `INPUT_STEM_sentiment.json`. Parent directories are created. Existing output files are overwritten, but using the input path as output is rejected before processing.

## Data contract

The input must be a JSON object containing a `reviews` list. Each entry must be an object with a nonempty string `text`. An optional `metadata` field must be an object. Invalid inputs fail before model initialization rather than silently dropping records. Empty review lists produce an empty annotated output without downloading or loading the model.

`analyze_sentiment` passes review texts to the pipeline in their existing order, retaining all original review fields, collection metadata, and other top-level fields. Each review gains a `sentiment` object containing a lowercase `label` (`positive`, `negative`, or `neutral`) and a numeric `score` between 0 and 1. An existing sentiment field is replaced. The score is the model's confidence in its predicted class, not a measure of sentiment intensity.

`metadata.sentiment_analysis` records the model ID, CPU device, batch size 8, truncation enabled, maximum length 512, analyzed count, and UTC analysis timestamp. The supplied pipeline settings are fixed rather than configurable. The text alone is classified; titles and star ratings do not influence the prediction. Text remains unchanged in the saved output. Long text is truncated by the tokenizer to 512 tokens, including special tokens.

The model is loaded locally through `pipeline(task="text-classification", model=MODEL, device="cpu")`; inference uses `classifier(texts, batch_size=8, truncation=True, max_length=512)`. The first nonempty run downloads and caches its tokenizer, configuration, and model weights from Hugging Face. Subsequent runs reuse the local cache. Inference occurs locally on CPU.

According to the [model card](https://huggingface.co/cardiffnlp/twitter-roberta-base-sentiment-latest), this model is intended for English and was trained on tweets. Predictions on app reviews are model estimates; this script does not validate them against human labels. No extra tweet preprocessing or language detection is applied.

Malformed JSON, invalid review structure, file errors, and model loading/inference errors handled by the CLI produce a message on stderr and status 1. Successful output returns status 0. Argument errors return status 2. No output is written until classification finishes successfully.

## Verification

`tests/test_analyze_sentiment.py` mocks the pipeline to verify the exact model/device/inference arguments, one-to-one prediction alignment, label normalization, preservation of reviews and collection metadata, invalid input rejection before initialization, empty collections, invalid predictions, CLI default output, and malformed JSON handling. These tests avoid model downloads.

Run all offline tests with `uv run python -m unittest discover -s tests -v`. A live model check uses the saved Instagram collection as input and checks that all 100 IDs remain in order, collection metadata is preserved, and every review has a valid sentiment label and score.
