import asyncio

from ..data import SensorData, WebcamData


class SensorBackend:

    def __init__(self, config: dict):
        """
        Initialize the sensor backend.
        :param config: configuration parameters
        """
        pass

    async def start(self):
        raise NotImplementedError()

    async def stop(self):
        raise NotImplementedError()

    async def get_data(self) -> SensorData|None:
        """
        Acquire and return the current reading, or None when the device had nothing to give.

        May block until the data is available: the daemon applies its own timeout and cancels the call.
        """
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
