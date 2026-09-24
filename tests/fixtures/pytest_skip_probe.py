import pytest


@pytest.mark.skip(reason="probe skip")
def test_probe() -> None:
    raise AssertionError("the skip probe must never execute")
