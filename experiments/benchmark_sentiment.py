import gc
import hashlib
import importlib.metadata
import json
import platform
import statistics
import time
from pathlib import Path

import hydra
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from hydra.core.hydra_config import HydraConfig
from omegaconf import OmegaConf
from transformers import AutoConfig, AutoTokenizer

from app.services.preprocessor import preprocess_collection
from app.services.sentiment import load_classifier
from app.settings import SENTIMENT_MODEL, SENTIMENT_REVISION, Settings


ONNX_FILES = {"onnx_fp32": "model.onnx", "onnx_int8": "model_int8.onnx", "onnx_uint8": "model_uint8.onnx"}


class OnnxClassifier:
    def __init__(self, session, tokenizer, labels, max_length):
        self.session = session
        self.tokenizer = tokenizer
        self.labels = labels
        self.max_length = max_length
        self.input_names = {item.name for item in session.get_inputs()}

    def __call__(self, texts, batch_size):
        predictions = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            encoded = self.tokenizer(batch, padding=True, truncation=True, max_length=self.max_length, return_tensors="np")
            logits = np.asarray(self.session.run(None, {name: encoded[name] for name in self.input_names})[0])
            if logits.shape != (len(batch), len(self.labels)) or not np.isfinite(logits).all():
                raise ValueError("ONNX returned invalid classification logits")
            probabilities = np.exp(logits - logits.max(axis=1, keepdims=True))
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            for row in probabilities:
                index = int(row.argmax())
                predictions.append({"label": self.labels[index], "score": float(row[index])})
        return predictions


def compare_predictions(reference, candidate, ids):
    if len(reference) != len(candidate) or len(reference) != len(ids):
        raise ValueError("Prediction counts must match the review count")
    changes = []
    for review_id, before, after in zip(ids, reference, candidate, strict=True):
        if before["label"].lower() != after["label"].lower():
            changes.append({"id": review_id, "reference": before["label"].lower(), "candidate": after["label"].lower()})
    return {
        "label_agreement": (len(ids) - len(changes)) / len(ids),
        "label_changes": changes,
        "max_confidence_delta": max(abs(a["score"] - b["score"]) for a, b in zip(reference, candidate, strict=True)),
    }


def load_runner(backend, config):
    if backend == "pytorch":
        classifier = load_classifier(Settings(sentiment_threads=config.threads))
        return lambda texts, batch_size: classifier(texts, batch_size=batch_size, truncation=True, max_length=config.max_length)
    path = hf_hub_download(config.onnx_model, "onnx/" + ONNX_FILES[backend], revision=config.onnx_revision)
    source_config = AutoConfig.from_pretrained(SENTIMENT_MODEL, revision=SENTIMENT_REVISION)
    onnx_config = AutoConfig.from_pretrained(config.onnx_model, revision=config.onnx_revision)
    if source_config.id2label != onnx_config.id2label:
        raise ValueError("ONNX and PyTorch label mappings differ")
    tokenizer = AutoTokenizer.from_pretrained(SENTIMENT_MODEL, revision=SENTIMENT_REVISION)
    options = ort.SessionOptions()
    options.intra_op_num_threads = config.threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(path, sess_options=options, providers=["CPUExecutionProvider"])
    return OnnxClassifier(session, tokenizer, source_config.id2label, config.max_length)


@hydra.main(version_base="1.3", config_path="../conf", config_name="sentiment_benchmark")
def main(config):
    if config.repeats < 1 or config.threads < 1 or not config.batch_sizes or any(size < 1 for size in config.batch_sizes):
        raise ValueError("Repeats, threads, and batch sizes must be positive")
    if not config.backends or config.backends[0] != "pytorch" or any(name not in {"pytorch", *ONNX_FILES} for name in config.backends):
        raise ValueError("Start with pytorch as the reference and select supported ONNX backends")
    data = json.loads(Path(config.input).read_text())
    prepared = preprocess_collection(data)
    texts = [review["text"] for review in prepared["reviews"]]
    ids = [review["id"] for review in prepared["reviews"]]
    if not texts:
        raise ValueError("Benchmark requires at least one review")
    output = Path(HydraConfig.get().runtime.output_dir) / "benchmark.json"
    result = {
        "input": str(config.input),
        "prepared_input_sha256": hashlib.sha256(json.dumps(list(zip(ids, texts)), ensure_ascii=False).encode()).hexdigest(),
        "review_count": len(texts), "platform": platform.platform(), "machine": platform.machine(),
        "versions": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "onnxruntime", "numpy")},
        "source_model": SENTIMENT_MODEL, "source_revision": SENTIMENT_REVISION,
        "config": OmegaConf.to_container(config, resolve=True), "results": [],
    }
    reference = None
    baseline_seconds = None
    for backend in config.backends:
        started = time.perf_counter()
        runner = load_runner(backend, config)
        load_seconds = time.perf_counter() - started
        for batch_size in config.batch_sizes:
            runner(texts[:batch_size], batch_size)
            elapsed = []
            for _ in range(config.repeats):
                started = time.perf_counter()
                predictions = runner(texts, batch_size)
                elapsed.append(time.perf_counter() - started)
            if reference is None:
                reference = predictions
                baseline_seconds = statistics.median(elapsed)
            comparison = compare_predictions(reference, predictions, ids)
            median = statistics.median(elapsed)
            row = {
                "backend": backend, "batch_size": batch_size, "load_seconds_including_download": load_seconds,
                "run_seconds": elapsed, "median_seconds": median, "speedup": baseline_seconds / median,
                **comparison,
                "predictions": [{"id": review_id, "label": item["label"].lower(), "score": item["score"]} for review_id, item in zip(ids, predictions, strict=True)],
            }
            result["results"].append(row)
            output.write_text(json.dumps(result, indent=2) + "\n")
            print(f"{backend:12} batch={batch_size} median={median:.3f}s speedup={row['speedup']:.2f}x agreement={comparison['label_agreement']:.1%}", flush=True)
        del runner
        gc.collect()
    print(f"Saved benchmark to {output}", flush=True)


if __name__ == "__main__":
    main()
