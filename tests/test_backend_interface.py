import asyncio

import pytest

from metarstation_daemon.backend import create_instance
from metarstation_daemon.backend.dummy import DummySensorBackend
from metarstation_daemon.backend.interface import (
    SensorBackend,
    WebcamBackend,
    WebcamBackendCallback,
)
from metarstation_daemon.data import WebcamData


class TestWebcamBackendCallback:

    async def test_delivers_the_snapshot(self, webcam_data):
        callback = WebcamBackendCallback()

        callback.update(webcam_data)

        assert await callback.get_data() is webcam_data

    async def test_waits_when_no_snapshot_is_available(self):
        callback = WebcamBackendCallback()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(callback.get_data(), timeout=0.05)

    async def test_waits_again_after_a_snapshot_was_consumed(self, webcam_data):
        callback = WebcamBackendCallback()
        callback.update(webcam_data)
        await callback.get_data()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(callback.get_data(), timeout=0.05)

    async def test_only_the_latest_snapshot_is_kept(self, webcam_data):
        callback = WebcamBackendCallback()
        stale = webcam_data
        fresh = WebcamData(expire=stale.expire, image_data=b'fresh', image_type=stale.image_type)

        callback.update(stale)
        callback.update(fresh)

        # an unconsumed snapshot is dropped: it would be expired by now anyway
        assert await callback.get_data() is fresh

    async def test_a_second_waiter_gets_nothing(self, webcam_data):
        """Only one consumer can win a snapshot; the other one is handed None, hence the
        falsiness check in the daemon data collection loop."""
        callback = WebcamBackendCallback()
        first = asyncio.create_task(callback.get_data())
        second = asyncio.create_task(callback.get_data())
        # let both tasks reach the wait
        await asyncio.sleep(0)

        callback.update(webcam_data)

        results = await asyncio.gather(first, second)
        assert sorted(result is None for result in results) == [False, True]


class TestAbstractInterfaces:
    """The base classes are contracts: a backend that forgets to implement one must fail loudly."""

    async def test_sensor_backend_is_abstract(self):
        backend = SensorBackend({})

        with pytest.raises(NotImplementedError):
            await backend.start()
        with pytest.raises(NotImplementedError):
            await backend.stop()
        with pytest.raises(NotImplementedError):
            await backend.get_data()

    async def test_webcam_backend_is_abstract(self):
        backend = WebcamBackend({}, WebcamBackendCallback())

        with pytest.raises(NotImplementedError):
            await backend.start()
        with pytest.raises(NotImplementedError):
            await backend.stop()

    def test_webcam_backend_keeps_the_callback(self):
        callback = WebcamBackendCallback()

        assert WebcamBackend({}, callback).callback is callback


class TestCreateInstance:

    def test_creates_a_backend_from_its_module_path(self):
        backend = create_instance('dummy.DummySensorBackend', {})

        assert isinstance(backend, DummySensorBackend)

    def test_forwards_the_configuration(self):
        backend = create_instance('dummy.DummySensorBackend', {'delay_secs': 5})

        # noinspection PyProtectedMember
        assert backend._delay_secs == 5

    def test_unknown_module_fails(self):
        with pytest.raises(ModuleNotFoundError):
            create_instance('nope.NopeBackend', {})

    def test_unknown_class_fails(self):
        with pytest.raises(AttributeError):
            create_instance('dummy.NopeBackend', {})
