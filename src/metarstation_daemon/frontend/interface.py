from ..data import SensorData, WebcamData


class DataFrontendError(Exception):
    """Sending failed for a temporary reason (e.g. server error): the same data can be sent again later."""


class DataRejectedError(DataFrontendError):
    """The frontend refused the data itself: sending the same data again would fail the same way."""


class DataFrontend:

    # noinspection PyUnusedLocal
    def __init__(self, config: dict):
        pass

    async def setup(self):
        raise NotImplementedError()

    async def send_data(self, data: list[SensorData]):
        raise NotImplementedError()

    async def send_webcam(self, data: WebcamData):
        raise NotImplementedError()
