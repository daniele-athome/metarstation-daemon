import asyncio
import datetime
import logging

from .interface import SensorBackend, SensorBackendQueue
from ..data import SensorData


_LOGGER = logging.getLogger(__name__)


class DummySensorBackend(SensorBackend):
    """A dummy sensor backend."""

    def __init__(self, config, queue: SensorBackendQueue):
        super().__init__(config, queue)
        self._data_collect_task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        _LOGGER.debug(f"Dummy sensor backend starting")
        self._data_collect_task = asyncio.get_running_loop().create_task(self._collect_data_start())
        self._running = True

    async def stop(self):
        _LOGGER.debug("Dummy sensor backend stopping")
        # this will trigger the shutdown mechanism in _collect_data_start
        self._running = False
        if self._data_collect_task:
            self._data_collect_task.cancel()

    async def _collect_data_start(self):
        while self._running:
            try:
                self.queue.push(SensorData(
                    timestamp=datetime.datetime.now(),
                    battery=100,
                    temperature=20.2,
                    humidity=64,
                    dew_point=13.16,
                    pressure=997.6,
                    illumination=0,
                    wind_speed=0,
                    gust_speed=0,
                    wind_direction=146,
                    uv_index=0,
                    raining=None,
                    precipitation=331.4,
                ))

            except asyncio.CancelledError:
                # we've been canceled, shutting down
                break
            except:
                _LOGGER.error("Unexpected error", exc_info=True)
                # TODO proper error handling

            # TODO make this delay a parameter
            await asyncio.sleep(10)
