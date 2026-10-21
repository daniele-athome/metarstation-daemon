import asyncio
import os
import shutil

import pytest

from metarstation_daemon.backend.interface import WebcamBackendCallback
from metarstation_daemon.backend.tapocam import TapoWebcamBackend

TEST_TIMEOUT_SECS = 5
"""Upper bound for stopping the backend in tests: hitting it means stopping hung."""


@pytest.fixture
def backend():
    backend = TapoWebcamBackend({
        'snapshot_interval_secs': 60,
        'cloud_username': 'admin',
        'cloud_password': 'secret',
        'ip_address': '192.168.1.11',
    }, WebcamBackendCallback())
    yield backend
    # noinspection PyProtectedMember
    shutil.rmtree(backend._tempdir, ignore_errors=True)


class TestStop:

    async def test_waits_for_the_snapshot_task_cleanup(self, backend):
        """The snapshot task cleans up in its finally: the temporary files must still be there by then."""
        # noinspection PyProtectedMember
        tempdir = backend._tempdir
        tempdir_on_cleanup = []
        started = asyncio.Event()

        async def snapshot_task():
            try:
                started.set()
                await asyncio.sleep(60)
            finally:
                # give the event loop a chance to run stop() concurrently, like a real cleanup would
                await asyncio.sleep(0.05)
                tempdir_on_cleanup.append(os.path.isdir(tempdir))

        task = asyncio.create_task(snapshot_task())
        # noinspection PyProtectedMember
        backend._snapshot_task = task
        await started.wait()

        await asyncio.wait_for(backend.stop(), TEST_TIMEOUT_SECS)

        assert task.done()
        assert tempdir_on_cleanup == [True]
        assert not os.path.exists(tempdir)

    async def test_stops_the_real_snapshot_loop(self, backend):
        # camera not connected: the loop just waits for the next round
        backend.streamer_connected()
        # noinspection PyProtectedMember
        task = backend._snapshot_task
        await asyncio.sleep(0)

        await asyncio.wait_for(backend.stop(), TEST_TIMEOUT_SECS)

        assert task.done()

    async def test_stop_without_a_snapshot_task(self, backend):
        # noinspection PyProtectedMember
        tempdir = backend._tempdir

        await asyncio.wait_for(backend.stop(), TEST_TIMEOUT_SECS)

        assert not os.path.exists(tempdir)
