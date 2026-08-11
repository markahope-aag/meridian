"""Tests for the watchdog's capture-depth stall detection.

The watchdog used to alert on depth alone, with the message "distill
likely wedged". Distill promotes at most 100 files per run and runs once
a day, so the ClientBrain sync landing ~2,000 documents put capture/ over
the threshold for weeks while the pipeline worked exactly as designed.

Two things went wrong as a result. The hourly watchdog returned an alert
every single run, which is the kind of permanently-red signal people stop
reading. And the message pointed at the wrong cause, so anyone acting on
it would have gone looking for a broken distill agent that was fine.

Depth is not a fault. Depth that stops going down is. These tests pin
that distinction, especially the case that was misfiring in production.

Run with: python -m pytest tests/test_watchdog_depth.py -v
"""

import importlib.util
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TMPDIR = tempfile.mkdtemp()
os.environ["MERIDIAN_ROOT"] = _TMPDIR

_AGENTS = Path(__file__).resolve().parent.parent / "agents"

spec = importlib.util.spec_from_file_location("watchdog", _AGENTS / "watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
sys.modules["watchdog"] = watchdog
spec.loader.exec_module(watchdog)


@pytest.fixture
def wd(tmp_path):
    """Watchdog pointed at a temp root with a stubbed depth reading."""
    (tmp_path / "capture").mkdir()
    (tmp_path / "state").mkdir()
    watchdog.ROOT = tmp_path
    watchdog.CAPTURE_DIR = tmp_path / "capture"
    watchdog.STATE_DIR = tmp_path / "state"
    return watchdog


def _set_depth(wd, count):
    wd._capture_depth = lambda: count


def _seed_state(wd, depth, hours_since_decrease):
    """Write the previous run's depth state."""
    last = datetime.now(timezone.utc) - timedelta(hours=hours_since_decrease)
    (wd.STATE_DIR / wd.CAPTURE_DEPTH_STATE_FILE).write_text(
        json.dumps({"depth": depth, "last_decrease_at": last.isoformat()}),
        encoding="utf-8",
    )


class TestNoFalseAlarms:
    def test_below_threshold_is_quiet(self, wd):
        _set_depth(wd, 100)
        assert wd.check_capture_queue_depth(dry_run=True)["alert"] is False

    def test_first_sighting_of_a_bulk_ingest_is_quiet(self, wd):
        """No prior state, so there is nothing to conclude yet."""
        _set_depth(wd, 1909)
        result = wd.check_capture_queue_depth(dry_run=True)
        assert result["alert"] is False
        assert result["over_threshold"] is True

    def test_deep_but_draining_is_quiet(self, wd):
        """The exact production case: 2009 -> 1909 after a nightly distill."""
        _set_depth(wd, 1909)
        _seed_state(wd, 2009, 30)
        result = wd.check_capture_queue_depth(dry_run=True)
        assert result["alert"] is False
        assert result["draining"] is True

    def test_flat_but_inside_one_distill_cycle_is_quiet(self, wd):
        """Distill is daily, so a few flat hours means nothing."""
        _set_depth(wd, 1909)
        _seed_state(wd, 1909, 2)
        assert wd.check_capture_queue_depth(dry_run=True)["alert"] is False

    def test_flat_just_under_the_window_is_quiet(self, wd):
        _set_depth(wd, 1909)
        _seed_state(wd, 1909, wd.CAPTURE_STALL_HOURS - 1)
        assert wd.check_capture_queue_depth(dry_run=True)["alert"] is False

    def test_shallow_and_flat_forever_is_quiet(self, wd):
        _set_depth(wd, 12)
        _seed_state(wd, 12, 99)
        assert wd.check_capture_queue_depth(dry_run=True)["alert"] is False


class TestRealStallsStillAlert:
    def test_deep_and_flat_past_the_window_alerts(self, wd):
        _set_depth(wd, 1909)
        _seed_state(wd, 1909, wd.CAPTURE_STALL_HOURS + 4)
        result = wd.check_capture_queue_depth(dry_run=True)
        assert result["alert"] is True
        assert "stalled" in result["details"][0]

    def test_deep_and_growing_alerts(self, wd):
        _set_depth(wd, 2100)
        _seed_state(wd, 1909, wd.CAPTURE_STALL_HOURS + 4)
        assert wd.check_capture_queue_depth(dry_run=True)["alert"] is True

    def test_alert_message_does_not_blame_distill_outright(self, wd):
        """The old message asserted a cause it had not established."""
        _set_depth(wd, 1909)
        _seed_state(wd, 1909, wd.CAPTURE_STALL_HOURS + 4)
        msg = wd.check_capture_queue_depth(dry_run=True)["details"][0]
        assert "has not decreased" in msg


class TestDepthStatePersistence:
    def test_state_is_written_on_a_real_run(self, wd):
        _set_depth(wd, 750)
        wd.check_capture_queue_depth(dry_run=False)
        state = json.loads(
            (wd.STATE_DIR / wd.CAPTURE_DEPTH_STATE_FILE).read_text(encoding="utf-8")
        )
        assert state["depth"] == 750
        assert state["last_decrease_at"]

    def test_dry_run_does_not_write_state(self, wd):
        _set_depth(wd, 750)
        wd.check_capture_queue_depth(dry_run=True)
        assert not (wd.STATE_DIR / wd.CAPTURE_DEPTH_STATE_FILE).exists()

    def test_decrease_resets_the_clock(self, wd):
        _set_depth(wd, 800)
        _seed_state(wd, 900, 40)
        result = wd.check_capture_queue_depth(dry_run=False)
        assert result["alert"] is False
        assert result["hours_since_decrease"] == 0.0

    def test_corrupt_state_file_is_survivable(self, wd):
        (wd.STATE_DIR / wd.CAPTURE_DEPTH_STATE_FILE).write_text(
            "{not json", encoding="utf-8"
        )
        _set_depth(wd, 1909)
        assert wd.check_capture_queue_depth(dry_run=True)["alert"] is False
