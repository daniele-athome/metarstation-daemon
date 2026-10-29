import asyncio
import os

import pytest

from metarstation_daemon.backend.tapocam import TapoStreamer

TEST_TIMEOUT_SECS = 5
"""Upper bound for pausing the stream in tests: hitting it means pausing hung."""


class FakeStreamer:
    """Stands in for pytapo's Streamer: only what pause_stream() touches."""

    def __init__(self, process, stop_error: Exception | None = None):
        self.streamProcess = process
        self.stop_error = stop_error
        self.stopped = False

    async def stop(self):
        self.stopped = True
        # pytapo re-raises the stream task's failure from stop()
        if self.stop_error:
            raise self.stop_error


@pytest.fixture
def streamer(tmp_path):
    return TapoStreamer(
        quality='HD',
        log_callback=None,
        connect_callback=None,
        tempdir=str(tmp_path),
        discovery_interface=None,
        discovery_username=None,
        discovery_password=None,
        camera_address='127.0.0.1',
        tapo_args={},
    )


@pytest.fixture
def stream_files(tmp_path):
    files = [tmp_path / 'stream.m3u8', tmp_path / 'stream0.ts']
    for f in files:
        f.write_text('')
    return files


async def _exited_process():
    process = await asyncio.create_subprocess_exec('true')
    await process.wait()
    return process


class TestPauseStream:

    async def test_kills_a_running_ffmpeg(self, streamer, stream_files):
        process = await asyncio.create_subprocess_exec('sleep', '60')
        fake = FakeStreamer(process)
        # noinspection PyProtectedMember
        streamer._streamer = fake

        await asyncio.wait_for(streamer.pause_stream(), TEST_TIMEOUT_SECS)

        assert process.returncode is not None
        assert fake.stopped
        # noinspection PyProtectedMember
        assert streamer._streamer is None
        assert not any(f.exists() for f in stream_files)

    async def test_ffmpeg_already_exited(self, streamer, stream_files):
        """The stream ended by itself (camera gone, no data): ffmpeg is no longer there to kill."""
        fake = FakeStreamer(await _exited_process())
        # noinspection PyProtectedMember
        streamer._streamer = fake

        await asyncio.wait_for(streamer.pause_stream(), TEST_TIMEOUT_SECS)

        assert fake.stopped
        # noinspection PyProtectedMember
        assert streamer._streamer is None
        assert not any(f.exists() for f in stream_files)

    async def test_stop_raises_the_stream_failure(self, streamer, stream_files):
        fake = FakeStreamer(await _exited_process(), stop_error=Exception('HTTP request returned 401 status code'))
        # noinspection PyProtectedMember
        streamer._streamer = fake

        await asyncio.wait_for(streamer.pause_stream(), TEST_TIMEOUT_SECS)

        # noinspection PyProtectedMember
        assert streamer._streamer is None
        assert not any(f.exists() for f in stream_files)

    async def test_without_a_stream(self, streamer, stream_files):
        await asyncio.wait_for(streamer.pause_stream(), TEST_TIMEOUT_SECS)

        # nothing to clean up: files of a stream we do not own are left alone
        assert all(os.path.exists(f) for f in stream_files)
