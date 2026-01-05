from ..data import SensorData

class DataFrontend:

    def __init__(self, config: dict):
        pass

    async def setup(self):
        raise NotImplementedError()

    async def send_data(self, data: list[SensorData]):
        raise NotImplementedError()
