import argparse
import asyncio
import functools
import logging
import signal
import tomllib
from io import BufferedReader
from typing import BinaryIO, IO

from weather_test.backends.interface import SensorBackend
from weather_test.backends.ws90 import WS90SensorBackend
from weather_test.data import SensorData
from weather_test.frontends.http import HTTPDataFrontend
from weather_test.frontends.interface import DataFrontend

_LOGGER = logging.getLogger(__name__)


class WeatherDaemon:
    def __init__(self, args):
        args_parser = argparse.ArgumentParser(
            prog='Weather Daemon',
            description='A weather test daemon',
            epilog='Not really of much help as of this moment.')
        args_parser.add_argument('-c', '--config', required=True, help='path to configuration file')
        # FIXME the 1: it's because of the uv shebang in the main script; we need a more stable solution though
        parsed_args = args_parser.parse_args(args[1:])

        config_file = parsed_args.config
        config_file_fp: BufferedReader | BinaryIO | IO[bytes]
        with open(config_file, 'rb') as config_file_fp:
            self.config = tomllib.load(config_file_fp)

        self._backend: SensorBackend = WS90SensorBackend(self.config['backend'])
        self._frontend: DataFrontend = HTTPDataFrontend(self.config['frontend'])
        self._collect_interval_secs = self.config['main']['collect_interval_secs']
        self._shutdown_event = asyncio.Event()
        self._failed_data: list[SensorData] = []

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

            #_LOGGER.debug("Collecting data")
            data = await self._backend.get_data()
            if data:
                try:
                    # we also send the data that failed during the previous attempt
                    await self._frontend.send_data(self._failed_data + data)
                    self._failed_data.clear()
                except:
                    # TODO proper exception handling
                    _LOGGER.warning("Failed to send data", exc_info=True)
                    # store the data for a later retry attempt
                    self._failed_data.extend(data)


def main(args):
    # TODO proper logging configuration
    formatter = "[%(asctime)s] %(name)s %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=formatter)
    logging.getLogger(__package__).setLevel(logging.DEBUG)
    # enable HTTP logging
    # logging.getLogger("httpx").setLevel(logging.DEBUG)
    # logging.getLogger("httpcore").setLevel(logging.DEBUG)

    asyncio.run(WeatherDaemon(args).run())
