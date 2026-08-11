"""Tests for synthesis queue status and priority sorting.

GET /synthesize/queue returned 500 with
"'<' not supported between instances of 'int' and 'str'".

populate items carry int priorities (0-100) and evolution-queued items
carry "high"/"medium"/"low". _priority_key exists to normalize exactly
that, and process_pending used it, but get_queue_status sorted with a
raw lambda instead.

The failure hid for as long as every pending item came from the
evolution detector, because comparing strings to strings happens to
work. Draining that queue left a mix of both kinds and the endpoint
broke immediately. These tests pin the mixed case, which is the one
that matters.

Run with: python -m pytest tests/test_synthesis_queue.py -v
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_TMPDIR = tempfile.mkdtemp()
os.environ["MERIDIAN_ROOT"] = _TMPDIR

_AGENTS = Path(__file__).resolve().parent.parent / "agents"

spec = importlib.util.spec_from_file_location(
    "synthesis_scheduler", _AGENTS / "synthesis_scheduler.py"
)
scheduler = importlib.util.module_from_spec(spec)
sys.modules["synthesis_scheduler"] = scheduler
spec.loader.exec_module(scheduler)


def _write_queue(items) -> Path:
    path = Path(_TMPDIR) / "synthesis_queue.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    scheduler.QUEUE_PATH = path
    return path


class TestPriorityKey:
    def test_int_priority(self):
        assert scheduler._priority_key({"priority": 50}) == 50.0

    def test_missing_priority_defaults_to_zero(self):
        assert scheduler._priority_key({}) == 0.0

    def test_string_priorities_are_ordered(self):
        high = scheduler._priority_key({"priority": "high"})
        medium = scheduler._priority_key({"priority": "medium"})
        low = scheduler._priority_key({"priority": "low"})
        assert high > medium > low

    def test_unknown_string_is_lowest(self):
        assert scheduler._priority_key({"priority": "urgent-ish"}) == 0.0

    def test_case_insensitive(self):
        assert scheduler._priority_key({"priority": "HIGH"}) == \
            scheduler._priority_key({"priority": "high"})

    def test_none_priority(self):
        assert scheduler._priority_key({"priority": None}) == 0.0

    def test_every_key_is_comparable(self):
        """The actual invariant: results must sort without raising."""
        items = [
            {"priority": "high"}, {"priority": 10}, {}, {"priority": None},
            {"priority": "low"}, {"priority": 3.5}, {"priority": "nonsense"},
        ]
        sorted(items, key=scheduler._priority_key, reverse=True)


class TestQueueStatusWithMixedPriorities:
    def test_mixed_int_and_string_priorities_does_not_raise(self):
        """This is the exact shape that 500'd the endpoint."""
        _write_queue([
            {"topic": "seo", "status": "pending", "priority": 0,
             "dimension": "knowledge"},
            {"topic": "analytics", "status": "pending", "priority": "high",
             "dimension": "knowledge", "queued_by": "evolution_detector"},
            {"topic": "ppc", "status": "complete", "priority": 5},
        ])
        status = scheduler.get_queue_status()
        assert status["pending"] == 2
        assert status["complete"] == 1

    def test_evolution_items_sort_ahead_of_default_priority(self):
        _write_queue([
            {"topic": "seo", "status": "pending", "priority": 0},
            {"topic": "analytics", "status": "pending", "priority": "high"},
        ])
        status = scheduler.get_queue_status()
        assert status["next_5"][0]["topic"] == "analytics"

    def test_all_string_priorities_still_work(self):
        """The case that masked the bug for months."""
        _write_queue([
            {"topic": "a", "status": "pending", "priority": "high"},
            {"topic": "b", "status": "pending", "priority": "low"},
        ])
        assert scheduler.get_queue_status()["pending"] == 2

    def test_empty_queue(self):
        _write_queue([])
        status = scheduler.get_queue_status()
        assert status["pending"] == 0
        assert status["next_5"] == []

    def test_layer4_candidates_are_not_counted_as_topics(self):
        _write_queue([
            {"topic": "seo", "status": "pending", "priority": 0},
            {"signal": "something", "type": "layer4_candidate",
             "status": "pending"},
        ])
        status = scheduler.get_queue_status()
        assert status["pending"] == 1
        assert status["layer4_candidates_pending"] == 1


# =========================================================================
# Parity between the two implementations
# =========================================================================

_RECEIVER = Path(__file__).resolve().parent.parent / "receiver"


def _load_receiver():
    """Import receiver/app.py against a temp MERIDIAN_ROOT."""
    os.environ["MERIDIAN_ROOT"] = _TMPDIR
    sys.path.insert(0, str(_RECEIVER))
    try:
        spec = importlib.util.spec_from_file_location(
            "receiver_app", _RECEIVER / "app.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["receiver_app"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(_RECEIVER))


try:
    receiver_app = _load_receiver()
except Exception:  # pragma: no cover - receiver deps missing
    receiver_app = None


PRIORITY_MATRIX = [
    {"priority": 0}, {"priority": 1}, {"priority": 100}, {"priority": -5},
    {"priority": 3.5}, {"priority": "high"}, {"priority": "medium"},
    {"priority": "low"}, {"priority": "HIGH"}, {"priority": "nonsense"},
    {"priority": None}, {"priority": True}, {"priority": False},
    {"priority": []}, {},
]


@pytest.mark.skipif(receiver_app is None, reason="receiver app not importable")
class TestReceiverSchedulerParity:
    """The receiver reimplements queue status instead of calling the
    scheduler. That duplication is why the bug survived being fixed once:
    the scheduler grew a tolerant key while the receiver kept a raw
    lambda. These tests fail if the two ever disagree again.
    """

    def test_priority_keys_agree_across_the_matrix(self):
        for item in PRIORITY_MATRIX:
            assert receiver_app._priority_key(item) == \
                scheduler._priority_key(item), f"disagreement on {item!r}"

    def test_both_sort_mixed_lists_without_raising(self):
        sorted(PRIORITY_MATRIX, key=receiver_app._priority_key, reverse=True)
        sorted(PRIORITY_MATRIX, key=scheduler._priority_key, reverse=True)

    def test_endpoint_returns_200_on_mixed_priorities(self):
        """The exact request that returned 500 in production."""
        queue = Path(_TMPDIR) / "synthesis_queue.json"
        queue.write_text(json.dumps([
            {"topic": "seo", "status": "pending", "priority": 0},
            {"topic": "analytics", "status": "pending", "priority": "high",
             "queued_by": "evolution_detector"},
        ]), encoding="utf-8")
        receiver_app.app.config["TESTING"] = True
        with receiver_app.app.test_client() as client:
            response = client.get("/synthesize/queue")
        assert response.status_code == 200
        assert response.get_json()["pending"] == 2
