from ..data import SensorData, WebcamData


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
