import argparse
import asyncio
import functools
import logging
import os
import signal
import tomllib
from collections import deque
from io import BufferedReader
from typing import BinaryIO, IO

from .backend.interface import SensorBackend, SensorBackendQueue, WebcamBackend, WebcamBackendCallback
from .backend.tapocam import TapoWebcamBackend
from .backend.ws90 import WS90SensorBackend
from .data import SensorData
from .frontend.http import HTTPDataFrontend
from .frontend.interface import DataFrontend

_LOGGER = logging.getLogger(__name__)

DATA_QUEUE_LIMIT = 200
"""Limit of the data queue used by sensor backends."""

FAILED_QUEUE_LIMIT = 200
"""Limit of the data queue for caching data that failed to send to the frontends."""


class WeatherDaemon:
    def __init__(self, args):
        args_parser = argparse.ArgumentParser(
            prog='Weather Daemon',
            description='A weather test daemon',
            epilog='Not really of much help as of this moment.')
        args_parser.add_argument('-c', '--config', required=True, help='path to configuration file')
        parsed_args = args_parser.parse_args(args[1:])

        config_file = parsed_args.config
        config_file_fp: BufferedReader | BinaryIO | IO[bytes]
        with open(config_file, 'rb') as config_file_fp:
            self.config = tomllib.load(config_file_fp)

        # sensor backend
        self._data_queue = deque(maxlen=DATA_QUEUE_LIMIT)
        self._backend: SensorBackend = WS90SensorBackend(self.config['backend'], SensorBackendQueue(self._data_queue))

        # webcam backend
        self._webcam: WebcamBackend|None = None
        self._webcam_callback: WebcamBackendCallback|None = None
        if 'webcam' in self.config:
            self._webcam_callback = WebcamBackendCallback()
            self._webcam: WebcamBackend|None = TapoWebcamBackend(self.config['webcam'], self._webcam_callback)

        # data upload frontend
        self._frontend: DataFrontend = HTTPDataFrontend(self.config['frontend'])

        self._collect_interval_secs = self.config['main']['collect_interval_secs']
        self._shutdown_event = asyncio.Event()
        self._failed_data: deque[SensorData] = deque(maxlen=FAILED_QUEUE_LIMIT)

    async def run(self):
        def sig_handler(code):
            _LOGGER.info("Received signal %s", code)
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, functools.partial(sig_handler, sig))

        # start collecting data from the device
        await self._backend.start()

        # start collecting images from the webcam
        if self._webcam:
            await self._webcam.start()

        # setup the data collection frontend
        await self._frontend.setup()

        # start collecting data
        data_collect_task = asyncio.get_running_loop().create_task(self._collect_data_start())

        await self._shutdown_event.wait()

        # cleanup
        data_collect_task.cancel()
        await self._backend.stop()
        if self._webcam:
            await self._webcam.stop()

    async def _collect_data_start(self):
        _LOGGER.debug("Starting data collection")

        while not self._shutdown_event.is_set():
            try:
                # wait for the shutdown event or the collect interval, whichever comes first
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._collect_interval_secs
                )
            except asyncio.TimeoutError:
                pass
            # we don't check for the shutdown event just yet, giving one last chance to send out the last data packet

            # _LOGGER.debug("Collecting data")
            # TODO is there a more "pythonic" way of doing this?
            # TODO we should collect all data available from the backend and either use max|min(timestamp) or an average
            data = []
            while len(self._data_queue) > 0:
                #data.append(self._data_queue.pop())
                data = [self._data_queue.pop()]
            if len(data) > 0:
                try:
                    # we also send the data that failed during the previous attempt
                    await self._frontend.send_data(list(self._failed_data) + data)
                    self._failed_data.clear()
                except:
                    # TODO proper exception handling
                    _LOGGER.warning("Failed to send data", exc_info=True)
                    # store the data for a later retry attempt
                    self._failed_data.extend(data)

            if self._webcam_callback:
                webcam_data = self._webcam_callback.get_data()
                if webcam_data:
                    try:
                        await self._frontend.send_webcam(webcam_data)
                    except:
                        # TODO proper exception handling
                        _LOGGER.warning("Failed to send webcam data", exc_info=True)


def is_journal_enabled():
    return 'JOURNAL_STREAM' in os.environ


def main(args):
    # TODO proper logging configuration
    if is_journal_enabled():
        formatter = "%(name)s %(levelname)s - %(message)s"
    else:
        formatter = "[%(asctime)s] %(name)s %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=formatter)
    logging.getLogger(__package__).setLevel(logging.DEBUG)
    # enable HTTP logging
    # logging.getLogger("httpx").setLevel(logging.DEBUG)
    # logging.getLogger("httpcore").setLevel(logging.DEBUG)

    asyncio.run(WeatherDaemon(args).run())
