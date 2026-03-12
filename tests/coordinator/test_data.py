from custom_components.localshift.coordinator.data import (
    AdaptiveParameters,
    CoordinatorData,
)


def test_adaptive_parameters_from_dict_ignores_invalid_last_updated():
    parameters = AdaptiveParameters.from_dict({
        "values": {"threshold": 1.5},
        "confidence": {"threshold": 0.9},
        "last_updated": "not-a-datetime",
        "update_count": 3,
    })

    assert parameters.values == {"threshold": 1.5}
    assert parameters.confidence == {"threshold": 0.9}
    assert parameters.last_updated is None
    assert parameters.update_count == 3


def test_coordinator_data_has_independent_load_deviation_diagnostics():
    first = CoordinatorData()
    second = CoordinatorData()

    first.load_deviation_diagnostics["status"] = "triggered"

    assert first.load_deviation_diagnostics == {"status": "triggered"}
    assert second.load_deviation_diagnostics == {}
