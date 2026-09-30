import asyncio

from ..data import SensorData, WebcamData


def _put_drop_oldest(queue: asyncio.Queue, item) -> None:
    """Put item, discarding the oldest element if the queue is full."""
    # try again until we can really put the new item
    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                # another consumer drained it in the meantime
                pass


class SensorBackendQueue:
    def __init__(self, queue):
        self._queue: asyncio.Queue[SensorData] = queue

    def push(self, data: SensorData):
        _put_drop_oldest(self._queue, data)


class SensorBackend:

    def __init__(self, config: dict, queue: SensorBackendQueue):
        """
        Initialize the sensor backend.
        :param config: configuration parameters
        :param queue: a queue interface to push sensor data
        """
        self.queue = queue

    async def start(self):
        raise NotImplementedError()

    async def stop(self):
        raise NotImplementedError()


class WebcamBackendCallback:
    def __init__(self):
        self._data: WebcamData|None = None
        self._event = asyncio.Event()

    def update(self, data: WebcamData):
        self._data = data
        self._event.set()

    async def get_data(self) -> WebcamData:
        await self._event.wait()
        data = self._data
        self._data = None
        self._event.clear()
        return data


class WebcamBackend:
    def __init__(self, config: dict, callback: WebcamBackendCallback):
        """
        Initialize the webcam backend.
        :param config: configuration parameters
        :param callback: a callback interface to update the webcam data
        """
        self.callback = callback

    async def start(self):
        raise NotImplementedError()

    async def stop(self):
        raise NotImplementedError()
