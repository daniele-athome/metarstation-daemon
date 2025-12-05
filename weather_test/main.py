import asyncio
import functools
import logging
import signal
from asyncio import Handle

from weather_test.backends.interface import SensorBackend
from weather_test.backends.ws90 import WS90SensorBackend
from weather_test.frontends.http import HTTPDataFrontend
from weather_test.frontends.interface import DataFrontend

_LOGGER = logging.getLogger(__name__)


class WeatherDaemon:
    def __init__(self, args):
        # TODO parameters
        self._backend: SensorBackend = WS90SensorBackend({'bt_address': '08:B9:5F:D4:2D:58'})
        # TODO parameters
        self._frontend: DataFrontend = HTTPDataFrontend({})
        # TODO parameter
        self._collect_interval_secs = 5
        self._shutdown_event = asyncio.Event()

    async def run(self):
        def sig_handler(code):
            _LOGGER.info("Received signal %s", code)
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, functools.partial(sig_handler, sig))

        # start collecting data from the device
        await self._backend.start()

        # setup the data collection frontend
        await self._frontend.setup()

        # start collecting data
        data_collect_task = asyncio.get_running_loop().create_task(self._collect_data_start())

        await self._shutdown_event.wait()

        # cleanup
        data_collect_task.cancel()
        await self._backend.stop()

    async def _collect_data_start(self):
        _LOGGER.debug("Starting data collection")

        while not self._shutdown_event.is_set():
            await asyncio.sleep(self._collect_interval_secs)

            _LOGGER.debug("Collecting data")
            data = await self._backend.get_data()
            if data:
                await self._frontend.send_data(data)


def main(args):
    # TODO proper logging configuration
    formatter = "[%(asctime)s] %(name)s %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=formatter)
    logging.getLogger(__package__).setLevel(logging.DEBUG)

    asyncio.run(WeatherDaemon(args).run())
