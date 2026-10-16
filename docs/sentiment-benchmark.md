# Sentiment inference benchmark

## Scope and decision

This experiment evaluates the pinned Xenova ONNX conversion against the existing CardiffNLP PyTorch classifier. The experiment does not change the API or inference backend. A subsequent rollout selects PyTorch batch size 1; the model, CPU, memory, and inference-thread settings stay the same. ONNX Runtime and NumPy are explicit development dependencies managed through uv; production dependency installation remains unchanged. No Apple, Gemini, or cloud compute calls are made by the benchmark. Initial model downloads use Hugging Face.

**Local result:** PyTorch batch size 1 was fastest, at 6.331 seconds versus the former production batch size 8 at 7.713 seconds. The 8-bit ONNX candidates were slower on this ARM Mac and changed sentiment labels. Do not deploy quantized weights based on these results; the target Linux x86 CPU still requires measurement.

## Reproduce

```bash
uv sync --locked
uv run --locked python -m experiments.benchmark_sentiment
```

Hydra configuration is `conf/sentiment_benchmark.yaml`. Results and resolved config go under ignored `hydra_outputs/sentiment_benchmark/`. To change one experiment setting:

```bash
uv run --locked python -m experiments.benchmark_sentiment threads=2
uv run --locked python -m experiments.benchmark_sentiment 'batch_sizes=[1]' repeats=5
```

Quote list overrides in shells that expand brackets: `'batch_sizes=[1]'`. Inputs must be a nonempty collection accepted by the existing preprocessor. The default input is the checked-in Nebula export, reused without collecting fresh reviews or calling Gemini.

## Method

Recorded on 2026-10-16, `macOS-27.0-arm64-arm-64bit`. One inference thread, CPU-only execution, 100 reviews, three complete timed runs per configuration; the table reports medians. Each configuration warms up one batch first. Preprocessing and model loading/downloads are outside the timed inference window; tokenization, batch padding, inference, and prediction formatting are inside it.

Source: `cardiffnlp/twitter-roberta-base-sentiment-latest` at `3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7`. ONNX: `Xenova/twitter-roberta-base-sentiment-latest` at `f3ec4d0925f90c3ca7ee7814f52d6ee7cf180445`. The files are `onnx/model.onnx`, `onnx/model_int8.onnx`, and `onnx/model_uint8.onnx`.

Both engines use the pinned source tokenizer, identical normalized review texts in original order, and truncation at 512 tokens. The benchmark checks that source and ONNX class mappings match. ONNX Runtime uses CPUExecutionProvider, one intra-op thread, one inter-op thread, sequential execution, and its default graph optimization settings. It does not run another Optimum export or custom graph-optimization pass.

Versions: Torch 2.14.1, Transformers 5.19.0, ONNX Runtime 1.31.0, NumPy 2.5.4. Prepared input SHA-256: `4c5bb8cce05be9c25cf5f099bb00ae8cc0969b2bf3d9920152c42082fdb95f5c`.

## Measured results

| Backend | Batch size | Median inference | Speed vs batch 8 | Label agreement |
|---|---:|---:|---:|---:|
| pytorch | 8 | 7.713 s | 1.00× | 100% |
| pytorch | 1 | 6.331 s | 1.22× | 100% |
| onnx_fp32 | 8 | 10.385 s | 0.74× | 100% |
| onnx_fp32 | 1 | 7.705 s | 1.00× | 100% |
| onnx_int8 | 8 | 11.741 s | 0.66× | 98% |
| onnx_int8 | 1 | 8.376 s | 0.92× | 98% |
| onnx_uint8 | 8 | 20.740 s | 0.37× | 99% |
| onnx_uint8 | 1 | 14.977 s | 0.51× | 99% |

Agreement is against the benchmark reference PyTorch batch-8 output, not human ground truth. PyTorch batch 1 preserved all 100 labels with maximum winning-confidence change below 0.000001. Full-precision ONNX preserved all labels with maximum winning-confidence change below 0.000003.

INT8 changed two labels at each batch size, but not the same two reviews: batch 8 changed one positive and one negative review to neutral, while batch 1 changed two positive reviews to neutral. UINT8 changed one positive review to negative at both batch sizes. These changes can alter negative evidence eligibility and downstream issue metrics; the effect is not limited to confidence scores. No claim is made that the changed labels are more or less accurate without a human-labeled evaluation.

Dynamic quantization can depend on the batch activation range, so changing batching can also change quantized results. The quantized ONNX weight files are about 126 MB versus 499 MB for full precision; smaller weights did not imply faster inference here. Peak runtime memory was not measured independently per backend.

## Limits and next experiment

These are ARM macOS results, not a Cloud Run benchmark. They cannot establish the speed of ONNX Runtime on Linux x86 or explain the entire observed cloud slowdown. Downloads/cache state differ across backend loads, so stored load durations are diagnostic rather than a fair cold-start comparison. Timings are not an SLA, and 100 examples do not establish classification accuracy.

Next, run the same input hash on the target Linux x86 environment with the same thread limits. Compare the current backend, batch size 1, full-precision ONNX, and the quantized candidates. Inspect changes to negative review IDs and review ambiguous/changed labels manually before any backend rollout. Keep token limits and preprocessing fixed during the comparison. The quantized backends remain experimental; production continues to use PyTorch.

The [raw measurements and per-review predictions](benchmarks/sentiment-macos-arm64.json) retain all three durations per configuration, winning-confidence differences, and changed IDs. Tests in `tests/test_sentiment_benchmark.py` verify batch order, tokenizer arguments, output shape/finiteness, and label comparison.

References: [Xenova model files](https://huggingface.co/Xenova/twitter-roberta-base-sentiment-latest/tree/f3ec4d0925f90c3ca7ee7814f52d6ee7cf180445/onnx), [CPU batching guidance](https://huggingface.co/docs/transformers/main_classes/pipelines#pipeline-batching), [ONNX Runtime quantization](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html).
