from scripts.audit_provider_boundaries import audit_provider_boundaries


def test_active_provider_capabilities_match_the_explicit_allowlist() -> None:
    report = audit_provider_boundaries()

    assert report["errors"] == []
    assert report["retired_endpoint_references"] == []
    assert report["summary"]["observations"] > 0
