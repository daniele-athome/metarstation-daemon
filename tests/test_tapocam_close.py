import asyncio
import logging
import threading

import pytest

from metarstation_daemon.backend import tapocam
from metarstation_daemon.backend.tapocam import TapoStreamer

TEST_TIMEOUT_SECS = 5
"""Upper bound for a reset in tests: hitting it means closing the session hung."""


class FakeTapo:
    """Stands in for pytapo's Tapo: only what _reset() touches."""

    def __init__(self, calls: list | None = None, close_error: Exception | None = None):
        self.calls = calls if calls is not None else []
        self.close_error = close_error
        self.close_count = 0
        self.close_thread: threading.Thread | None = None

    def close(self):
        self.close_count += 1
        self.close_thread = threading.current_thread()
        self.calls.append('close')
        if self.close_error:
            raise self.close_error


class FakeStreamer:
    """Stands in for pytapo's Streamer, recording the pause in the shared call log."""

    def __init__(self, calls: list):
        self.calls = calls
        self.streamProcess = None

    async def stop(self):
        self.calls.append('stop')


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


class TestReset:

    async def test_closes_the_camera_session(self, streamer):
        tapo = FakeTapo()
        # noinspection PyProtectedMember
        streamer._tapo = tapo

        # noinspection PyProtectedMember
        await asyncio.wait_for(streamer._reset(), TEST_TIMEOUT_SECS)

        assert tapo.close_count == 1
        # noinspection PyProtectedMember
        assert streamer._tapo is None
        assert not streamer.ready

    async def test_closes_off_the_event_loop(self, streamer):
        """pytapo's close() runs an event loop of its own, which it cannot do on ours."""
        tapo = FakeTapo()
        # noinspection PyProtectedMember
        streamer._tapo = tapo

        # noinspection PyProtectedMember
        await asyncio.wait_for(streamer._reset(), TEST_TIMEOUT_SECS)

        assert tapo.close_count == 1
        assert tapo.close_thread is not threading.current_thread()

    async def test_pauses_the_stream_before_closing(self, streamer):
        """The streamer holds a media session on the Tapo, so it has to go first."""
        calls = []
        # noinspection PyProtectedMember
        streamer._streamer = FakeStreamer(calls)
        # noinspection PyProtectedMember
        streamer._tapo = FakeTapo(calls)

        # noinspection PyProtectedMember
        await asyncio.wait_for(streamer._reset(), TEST_TIMEOUT_SECS)

        assert calls == ['stop', 'close']

    async def test_survives_a_failing_close(self, streamer, caplog):
        tapo = FakeTapo(close_error=Exception('Temporary Suspension'))
        # noinspection PyProtectedMember
        streamer._tapo = tapo

        with caplog.at_level(logging.WARNING):
            # noinspection PyProtectedMember
            await asyncio.wait_for(streamer._reset(), TEST_TIMEOUT_SECS)

        # the session is gone for us either way, retrying on it would be worse
        # noinspection PyProtectedMember
        assert streamer._tapo is None
        assert 'Temporary Suspension' in caplog.text

    async def test_without_a_camera(self, streamer):
        # noinspection PyProtectedMember
        await asyncio.wait_for(streamer._reset(), TEST_TIMEOUT_SECS)

        # noinspection PyProtectedMember
        assert streamer._tapo is None

    async def test_stop_closes_the_camera_session(self, streamer):
        tapo = FakeTapo()
        # noinspection PyProtectedMember
        streamer._tapo = tapo

        await asyncio.wait_for(streamer.stop(), TEST_TIMEOUT_SECS)

        assert tapo.close_count == 1

    async def test_restart_closes_the_old_camera_session(self, streamer):
        tapo = FakeTapo()
        # noinspection PyProtectedMember
        streamer._tapo = tapo
        started = []
        streamer.start = lambda: _record(started)

        await asyncio.wait_for(streamer.restart(), TEST_TIMEOUT_SECS)

        assert tapo.close_count == 1
        assert started == ['start']


async def _record(calls: list):
    calls.append('start')


class TestCreateTapo:
    """_create_tapo() runs in an executor, where cancelling the connect task cannot reach it."""

    @pytest.fixture
    def fake_tapo_class(self, monkeypatch):
        created = []

        def factory(**kwargs):
            tapo = FakeTapo()
            created.append(tapo)
            return tapo

        monkeypatch.setattr(tapocam, 'Tapo', factory)
        return created

    def test_returns_the_camera(self, streamer, fake_tapo_class):
        # noinspection PyProtectedMember
        tapo = streamer._create_tapo('127.0.0.1', streamer._connect_generation)

        assert tapo is fake_tapo_class[0]
        assert tapo.close_count == 0

    def test_closes_a_camera_superseded_by_a_reset(self, streamer, fake_tapo_class):
        # noinspection PyProtectedMember
        stale_generation = streamer._connect_generation - 1

        # noinspection PyProtectedMember
        tapo = streamer._create_tapo('127.0.0.1', stale_generation)

        assert tapo is None
        assert fake_tapo_class[0].close_count == 1
