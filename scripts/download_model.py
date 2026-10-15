import argparse
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

from app.settings import SENTIMENT_MODEL, SENTIMENT_REVISION


def main():
    parser = argparse.ArgumentParser(description="Download the pinned CPU sentiment model for the container")
    parser.add_argument("--output", type=Path, default=Path("models/sentiment"))
    args = parser.parse_args()
    files = {item.rfilename for item in HfApi().model_info(SENTIMENT_MODEL, revision=SENTIMENT_REVISION).siblings}
    weights = "model.safetensors" if "model.safetensors" in files else "pytorch_model.bin"
    snapshot_download(
        repo_id=SENTIMENT_MODEL, revision=SENTIMENT_REVISION, local_dir=args.output,
        allow_patterns=[weights, "config.json", "tokenizer*", "vocab.json", "merges.txt", "special_tokens_map.json", "README.md", "LICENSE*"],
    )
    print(f"Downloaded revision {SENTIMENT_REVISION} to {args.output}")


if __name__ == "__main__":
    main()
