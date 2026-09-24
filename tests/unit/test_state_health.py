from novel_writer.services.state_health import (
    normalize_safe_pollution,
    scan_state_pollution,
)


def test_state_health_detects_and_safely_normalizes_empty_array_stringification() -> None:
    state = {
        "characters": [
            {
                "name": "凌风",
                "forbidden_behaviors": "System.Object[]",
                "mind_state": {"beliefs": "System.Object[]"},
            }
        ]
    }

    findings = scan_state_pollution(state)
    repaired, repairs, manual = normalize_safe_pollution(state)

    assert findings
    assert all(item["safe_repair"] for item in findings)
    assert repaired["characters"][0]["forbidden_behaviors"] == []
    assert repaired["characters"][0]["mind_state"]["beliefs"] == []
    assert len(repairs) == 2
    assert manual == []


def test_state_health_refuses_lossy_powershell_object_reconstruction() -> None:
    state = {
        "characters": [
            {"name": "凌风", "mind_state": "@{beliefs=System.Object[]}"}
        ]
    }

    _, repairs, manual = normalize_safe_pollution(state)

    assert repairs == []
    assert manual
    assert manual[0]["safe_repair"] is False
