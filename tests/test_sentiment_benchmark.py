from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from experiments.benchmark_sentiment import OnnxClassifier, compare_predictions


def test_onnx_batches_preserve_order_labels_and_tokenization():
    session = Mock()
    session.get_inputs.return_value = [SimpleNamespace(name="input_ids"), SimpleNamespace(name="attention_mask")]
    session.run.side_effect = [[np.array([[8., 0., -1.], [0., 8., -1.]])], [np.array([[0., -1., 8.]])]]
    tokenizer = Mock(side_effect=lambda batch, **kwargs: {"input_ids": np.ones((len(batch), 4), dtype=np.int64), "attention_mask": np.ones((len(batch), 4), dtype=np.int64), "extra": np.zeros((len(batch), 4))})
    classifier = OnnxClassifier(session, tokenizer, {0: "negative", 1: "neutral", 2: "positive"}, 512)
    predictions = classifier(["broken", "ordinary", "excellent"], batch_size=2)
    assert [item["label"] for item in predictions] == ["negative", "neutral", "positive"]
    assert all(.99 < item["score"] < 1 for item in predictions)
    assert [call.args[0] for call in tokenizer.call_args_list] == [["broken", "ordinary"], ["excellent"]]
    assert all(call.kwargs == {"padding": True, "truncation": True, "max_length": 512, "return_tensors": "np"} for call in tokenizer.call_args_list)
    assert all(set(call.args[1]) == {"input_ids", "attention_mask"} for call in session.run.call_args_list)


@pytest.mark.parametrize("logits", [np.array([[float("nan"), 0., 1.]]), np.array([[0., 1.]]), np.array([[0., 1., 2.], [0., 1., 2.]])])
def test_invalid_onnx_predictions_are_rejected(logits):
    session = Mock()
    session.get_inputs.return_value = [SimpleNamespace(name="input_ids")]
    session.run.return_value = [logits]
    classifier = OnnxClassifier(session, lambda *args, **kwargs: {"input_ids": np.ones((1, 2), dtype=np.int64)}, {0: "negative", 1: "neutral", 2: "positive"}, 512)
    with pytest.raises(ValueError, match="invalid classification logits"):
        classifier(["review"], 1)


def test_comparison_reports_label_changes_without_claiming_accuracy():
    reference = [{"label": "Negative", "score": .8}, {"label": "neutral", "score": .6}]
    candidate = [{"label": "negative", "score": .7}, {"label": "positive", "score": .9}]
    result = compare_predictions(reference, candidate, ["a", "b"])
    assert result["label_agreement"] == .5
    assert result["label_changes"] == [{"id": "b", "reference": "neutral", "candidate": "positive"}]
    assert result["max_confidence_delta"] == pytest.approx(.3)
    with pytest.raises(ValueError, match="counts"):
        compare_predictions(reference, candidate[:1], ["a", "b"])
