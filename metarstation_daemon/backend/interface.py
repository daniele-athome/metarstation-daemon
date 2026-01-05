from collections import deque

from ..data import SensorData, WebcamData


class SensorBackendQueue:
    def __init__(self, queue):
        self._queue: deque[SensorData] = queue

    def push(self, data: SensorData):
        self._queue.appendleft(data)


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

    def update(self, data: WebcamData):
        self._data = data

    def get_data(self) -> WebcamData:
        if self._data is None:
            raise ValueError('No data available')
        data = self._data
        self._data = None
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
