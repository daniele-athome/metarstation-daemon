import logging

from .interface import DataFrontend
from ..data import SensorData

_LOGGER = logging.getLogger(__name__)


class HTTPDataFrontend(DataFrontend):

    def __init__(self, config: dict):
        super().__init__(config)
        # TODO

    async def setup(self):
        # TODO
        _LOGGER.debug("HTTP data frontend starting")

    async def send_data(self, data: list[SensorData]):
        _LOGGER.debug(f"Sending data: {data}")
        # TODO
