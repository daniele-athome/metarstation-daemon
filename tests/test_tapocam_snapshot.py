import asyncio
import os
import shutil

import pytest

from metarstation_daemon.backend import tapocam
from metarstation_daemon.backend.interface import WebcamBackendCallback
from metarstation_daemon.backend.tapocam import TapoWebcamBackend

TEST_TIMEOUT_SECS = 5
"""Upper bound for a single snapshot in tests: hitting it means the snapshot hung."""


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """Puts a fake ffmpeg first in PATH; the test provides the body of its shell script."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    monkeypatch.setenv('PATH', f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def install(script: str):
        ffmpeg = bin_dir / 'ffmpeg'
        ffmpeg.write_text(f"#!/bin/sh\n{script}\n")
        ffmpeg.chmod(0o755)

    return install


@pytest.fixture
def backend():
    backend = TapoWebcamBackend({
        'snapshot_interval_secs': 60,
        'cloud_username': 'admin',
        'cloud_password': 'secret',
    }, WebcamBackendCallback())
    yield backend
    # noinspection PyProtectedMember
    shutil.rmtree(backend._tempdir, ignore_errors=True)


async def _assert_no_snapshot(callback: WebcamBackendCallback):
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(callback.get_data(), timeout=0.05)


async def _read_pid(pid_file) -> int:
    """Waits for the fake ffmpeg to write its PID."""
    async with asyncio.timeout(TEST_TIMEOUT_SECS):
        while not pid_file.exists() or not pid_file.read_text().endswith('\n'):
            await asyncio.sleep(0.01)
    return int(pid_file.read_text())


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class TestTakeSnapshot:

    async def test_delivers_the_snapshot(self, backend, fake_ffmpeg):
        fake_ffmpeg("printf 'JPEG'")

        # noinspection PyProtectedMember
        await asyncio.wait_for(backend._take_snapshot(), TEST_TIMEOUT_SECS)

        data = await asyncio.wait_for(backend.callback.get_data(), TEST_TIMEOUT_SECS)
        assert data.image_data == b'JPEG'
        assert data.image_type == 'image/jpeg'

    async def test_does_not_deadlock_on_verbose_stderr(self, backend, fake_ffmpeg):
        """ffmpeg writing more than a pipe buffer to stderr must not block the snapshot."""
        fake_ffmpeg("head -c 1000000 /dev/zero >&2; printf 'JPEG'")

        # noinspection PyProtectedMember
        await asyncio.wait_for(backend._take_snapshot(), TEST_TIMEOUT_SECS)

        data = await asyncio.wait_for(backend.callback.get_data(), TEST_TIMEOUT_SECS)
        assert data.image_data == b'JPEG'

    async def test_failed_ffmpeg_delivers_nothing(self, backend, fake_ffmpeg):
        fake_ffmpeg("echo 'stream not found' >&2; exit 1")

        # noinspection PyProtectedMember
        await asyncio.wait_for(backend._take_snapshot(), TEST_TIMEOUT_SECS)

        await _assert_no_snapshot(backend.callback)

    async def test_hung_ffmpeg_is_killed_on_timeout(self, backend, fake_ffmpeg, tmp_path, monkeypatch):
        monkeypatch.setattr(tapocam, '_SNAPSHOT_TIMEOUT_SECS', 0.2)
        pid_file = tmp_path / 'ffmpeg.pid'
        fake_ffmpeg(f"echo $$ > {pid_file}; exec sleep 60")

        # noinspection PyProtectedMember
        await asyncio.wait_for(backend._take_snapshot(), TEST_TIMEOUT_SECS)

        assert not _is_running(await _read_pid(pid_file))
        await _assert_no_snapshot(backend.callback)

    async def test_ffmpeg_is_killed_on_cancel(self, backend, fake_ffmpeg, tmp_path):
        pid_file = tmp_path / 'ffmpeg.pid'
        fake_ffmpeg(f"echo $$ > {pid_file}; exec sleep 60")
        # noinspection PyProtectedMember
        task = asyncio.create_task(backend._take_snapshot())
        pid = await _read_pid(pid_file)

        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, TEST_TIMEOUT_SECS)
        assert not _is_running(pid)
