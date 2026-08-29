import numpy as np

from wake_word.detector import OpenWakeWordDetector

CHUNK = np.zeros(480, dtype=np.int16)


class ScriptedModel:
    def __init__(self, scores):
        self._scores = iter(scores)
        self.reset_calls = 0

    def __call__(self, chunk):
        return next(self._scores)

    def reset(self):
        self.reset_calls += 1


def test_rising_edge_triggers_once():
    model = ScriptedModel([0.9])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    assert detector.process_chunk(CHUNK) is True


def test_sustained_high_score_does_not_retrigger():
    model = ScriptedModel([0.9, 0.9, 0.9])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    assert detector.process_chunk(CHUNK) is True
    assert detector.process_chunk(CHUNK) is False
    assert detector.process_chunk(CHUNK) is False


def test_drop_below_threshold_then_rise_retriggers():
    model = ScriptedModel([0.9, 0.1, 0.9])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    assert detector.process_chunk(CHUNK) is True
    assert detector.process_chunk(CHUNK) is False
    assert detector.process_chunk(CHUNK) is True


def test_score_below_threshold_never_triggers():
    model = ScriptedModel([0.1, 0.2, 0.0])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    assert detector.process_chunk(CHUNK) is False
    assert detector.process_chunk(CHUNK) is False
    assert detector.process_chunk(CHUNK) is False


def test_score_exactly_at_threshold_triggers():
    model = ScriptedModel([0.5])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    assert detector.process_chunk(CHUNK) is True


def test_reset_rearms_without_score_dropping():
    model = ScriptedModel([0.9, 0.9])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    assert detector.process_chunk(CHUNK) is True
    detector.reset()
    assert detector.process_chunk(CHUNK) is True


def test_reset_calls_underlying_model_reset():
    model = ScriptedModel([0.9])
    detector = OpenWakeWordDetector(model_fn=model, threshold=0.5)

    detector.process_chunk(CHUNK)
    detector.reset()

    assert model.reset_calls == 1


def test_reset_is_safe_when_model_fn_has_no_reset():
    def plain_model(chunk):
        return 0.1

    detector = OpenWakeWordDetector(model_fn=plain_model, threshold=0.5)
    detector.reset()  # must not raise
