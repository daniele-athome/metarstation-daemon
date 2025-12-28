from collections import deque

from ..data import SensorData


class SensorBackendQueue:
    def __init__(self, queue):
        self._queue: deque = queue

    def push(self, data: SensorData):
        self._queue.append(data)


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
