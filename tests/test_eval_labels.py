from tests.eval_ai import SAMPLE_LABELS, STATUSES, pair_key, sample_pairs


def test_sample_labels_match_real_pairs():
    """If the sample docs change, the hand labels must be revisited."""
    keys = {pair_key(*p) for p in sample_pairs()}
    assert len(keys) == 34
    assert set(SAMPLE_LABELS) <= keys
    assert set(SAMPLE_LABELS.values()) <= set(STATUSES)
