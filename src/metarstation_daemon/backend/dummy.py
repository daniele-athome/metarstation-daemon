import asyncio
import datetime
import logging

from .interface import SensorBackend
from ..data import SensorData


BATTERY_KEY = 'dummy'
"""Battery dict key for the dummy device."""

_LOGGER = logging.getLogger(__name__)


class DummySensorBackend(SensorBackend):
    """A dummy sensor backend."""

    def __init__(self, config):
        super().__init__(config)
        # artificial delay before answering, to exercise the collection timeout
        self._delay_secs = config.get('delay_secs', 0)

    async def start(self):
        _LOGGER.debug(f"Dummy sensor backend starting")

    async def stop(self):
        _LOGGER.debug("Dummy sensor backend stopping")

    async def get_data(self) -> SensorData|None:
        if self._delay_secs:
            await asyncio.sleep(self._delay_secs)

        return SensorData(
            timestamp=datetime.datetime.now(datetime.UTC),
            battery={BATTERY_KEY: 100},
            temperature=20.2,
            humidity=64,
            dew_point=13.16,
            pressure=997.6,
            illumination=0,
            wind_speed=3,
            gust_speed=0,
            wind_direction=45,
            uv_index=0,
            raining=None,
            precipitation=331.4,
        )
