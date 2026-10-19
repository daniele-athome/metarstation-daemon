import asyncio

import pytest

from metarstation_daemon.backend import create_instance
from metarstation_daemon.backend.dummy import DummySensorBackend
from metarstation_daemon.backend.interface import (
    SensorBackend,
    SensorBackendQueue,
    WebcamBackend,
    WebcamBackendCallback,
    _put_drop_oldest,
)
from metarstation_daemon.data import SensorData, WebcamData


class TestPutDropOldest:
    """The data queue must never block the sensor backend, even when nobody is consuming."""

    def test_puts_on_a_queue_with_room(self):
        queue = asyncio.Queue(maxsize=2)

        _put_drop_oldest(queue, 'a')

        assert queue.get_nowait() == 'a'

    def test_drops_the_oldest_item_when_full(self):
        queue = asyncio.Queue(maxsize=2)
        _put_drop_oldest(queue, 'a')
        _put_drop_oldest(queue, 'b')

        _put_drop_oldest(queue, 'c')

        assert queue.qsize() == 2
        # 'a' is gone, the survivors keep their order
        assert [queue.get_nowait(), queue.get_nowait()] == ['b', 'c']

    def test_keeps_dropping_while_the_queue_stays_full(self):
        queue = asyncio.Queue(maxsize=1)

        for item in ('a', 'b', 'c'):
            _put_drop_oldest(queue, item)

        assert queue.qsize() == 1
        assert queue.get_nowait() == 'c'

    def test_never_drops_on_an_unbounded_queue(self):
        queue = asyncio.Queue()

        for item in range(100):
            _put_drop_oldest(queue, item)

        assert queue.qsize() == 100


def test_sensor_backend_queue_pushes_to_the_underlying_queue():
    queue = asyncio.Queue(maxsize=2)
    data = SensorData()

    SensorBackendQueue(queue).push(data)

    assert queue.get_nowait() is data


def test_sensor_backend_queue_drops_the_oldest_reading_when_full():
    queue = asyncio.Queue(maxsize=1)
    backend_queue = SensorBackendQueue(queue)
    stale = SensorData()
    fresh = SensorData()

    backend_queue.push(stale)
    backend_queue.push(fresh)

    # a stale reading is worth less than the latest one
    assert queue.get_nowait() is fresh


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
        backend = SensorBackend({}, SensorBackendQueue(asyncio.Queue()))

        with pytest.raises(NotImplementedError):
            await backend.start()
        with pytest.raises(NotImplementedError):
            await backend.stop()

    async def test_webcam_backend_is_abstract(self):
        backend = WebcamBackend({}, WebcamBackendCallback())

        with pytest.raises(NotImplementedError):
            await backend.start()
        with pytest.raises(NotImplementedError):
            await backend.stop()

    def test_sensor_backend_keeps_the_queue(self):
        queue = SensorBackendQueue(asyncio.Queue())

        assert SensorBackend({}, queue).queue is queue

    def test_webcam_backend_keeps_the_callback(self):
        callback = WebcamBackendCallback()

        assert WebcamBackend({}, callback).callback is callback


class TestCreateInstance:

    def test_creates_a_backend_from_its_module_path(self):
        queue = SensorBackendQueue(asyncio.Queue())

        backend = create_instance('dummy.DummySensorBackend', {}, queue)

        assert isinstance(backend, DummySensorBackend)
        assert backend.queue is queue

    def test_forwards_the_configuration(self):
        backend = create_instance(
            'dummy.DummySensorBackend', {'sleep_secs': 5}, SensorBackendQueue(asyncio.Queue()))

        # noinspection PyProtectedMember
        assert backend._sleep_secs == 5

    def test_unknown_module_fails(self):
        with pytest.raises(ModuleNotFoundError):
            create_instance('nope.NopeBackend', {}, SensorBackendQueue(asyncio.Queue()))

    def test_unknown_class_fails(self):
        with pytest.raises(AttributeError):
            create_instance('dummy.NopeBackend', {}, SensorBackendQueue(asyncio.Queue()))
