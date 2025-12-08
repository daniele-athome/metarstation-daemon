from ..data import SensorData

class SensorBackend:

    def __init__(self, config: dict):
        pass

    async def start(self):
        raise NotImplementedError()

    async def stop(self):
        raise NotImplementedError()

    async def get_data(self) -> list[SensorData]:
        """Retrieves the latest batch of data to be sent to the frontend."""
        raise NotImplementedError()
