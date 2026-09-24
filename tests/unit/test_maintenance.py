import subprocess
import sys

import pytest

from novel_writer.core.maintenance import storage_lease


def test_storage_lease_excludes_another_process_and_releases_on_crash(tmp_path):
    content = tmp_path / "content"
    code = (
        "import sys,time; from pathlib import Path; "
        "from novel_writer.core.maintenance import storage_lease; "
        "lease=storage_lease(Path(sys.argv[1])); lease.__enter__(); "
        "print('locked',flush=True); time.sleep(30)"
    )
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(content)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match="内容目录"), storage_lease(content):
            pytest.fail("second owner must be rejected")
    finally:
        child.kill()
        child.communicate(timeout=5)
    with storage_lease(content):
        assert (tmp_path / ".maintenance.lock").is_file()


def test_storage_lease_releases_after_exception_without_deleting_file(tmp_path):
    with pytest.raises(ValueError), storage_lease(tmp_path / "content"):
        raise ValueError("fixture")
    with storage_lease(tmp_path / "content"):
        assert (tmp_path / ".maintenance.lock").is_file()
    assert (tmp_path / ".maintenance.lock").read_bytes() == b"0"
