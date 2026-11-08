import pytest

from agent4pharma.runtime.locking import worker_lock


def test_second_instance_has_clear_error_and_lock_is_released(tmp_path):
    with worker_lock(tmp_path):
        with pytest.raises(RuntimeError, match="process|进程"):
            with worker_lock(tmp_path):
                pytest.fail("Second instance acquired an active lock")
    with worker_lock(tmp_path):
        assert (tmp_path / "worker.lock").stat().st_size >= 1
