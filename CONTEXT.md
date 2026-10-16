# Review analysis

## Language

**Interpretation**: Assigning textual sentiment and identifying evidence-backed product complaints and recommended actions in a collection of reviews.

**Fast mode**: Interpretation with sentiment and issue analysis produced together by Gemini.

**Quality mode**: Interpretation with RoBERTa sentiment followed by Gemini issue analysis. The name identifies the method, not a measured accuracy guarantee.

**Issue support**: The unique reviews whose quoted complaints support an issue, regardless of their overall sentiment.

**Negative issue coverage**: The fraction of negative reviews supporting at least one issue. Undefined when there are no negative reviews.
