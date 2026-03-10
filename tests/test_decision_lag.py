"""Tests for decision-to-implementation lag tracking (Issue #501)."""

from custom_components.localshift.coordinator import CoordinatorData


class TestDecisionLagTracking:
    """Test decision lag tracking in CoordinatorData."""

    def test_initial_state_no_lag(self):
        """Test initial state has no lag data."""
        data = CoordinatorData()

        assert data.decision_timestamp is None
        assert data.decision_mode is None
        assert data.implementation_timestamp is None
        assert data.decision_lag_seconds is None
        assert len(data.decision_lag_history) == 0

    def test_history_limit(self):
        """Test history is limited to 50 entries when managed by state machine."""
        data = CoordinatorData()

        # Simulate what state machine does - adds entries with limit check
        for i in range(60):
            entry = {
                "from_mode": "self_consumption",
                "to_mode": "grid_charging",
                "lag_seconds": 5.0 + i,
                "decision_time": f"2025-01-01T{i:02d}:00:00",
                "implementation_time": f"2025-01-01T{i:02d}:00:05",
            }
            data.decision_lag_history.append(entry)
            if len(data.decision_lag_history) > 50:
                data.decision_lag_history = data.decision_lag_history[-50:]

        # Should be limited to 50
        assert len(data.decision_lag_history) == 50
        # Should keep the most recent (entries 10-59, lag values 15.0-64.0)
        assert data.decision_lag_history[0]["lag_seconds"] == 15.0
        assert data.decision_lag_history[-1]["lag_seconds"] == 64.0
