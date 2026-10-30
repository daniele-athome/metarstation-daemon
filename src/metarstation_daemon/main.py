import argparse
import asyncio
import functools
import logging
import os
import signal
import sys
import tomllib
from collections import deque
from io import BufferedReader
from typing import BinaryIO, IO, TYPE_CHECKING

from .backend import create_instance as create_backend_instance
from .backend.interface import SensorBackend, SensorBackendQueue, WebcamBackend, WebcamBackendCallback
from .data import SensorData
from .frontend.http import HTTPDataFrontend
from .frontend.interface import DataFrontend, DataRejectedError

if TYPE_CHECKING:
    from .backend.tapocam import TapoWebcamBackend
    from .dashboard import StaticDashboardGenerator


_LOGGER = logging.getLogger(__name__)

DATA_QUEUE_LIMIT = 200
"""Limit of the data queue used by sensor backends."""

FAILED_QUEUE_LIMIT = 50
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
        self._data_queue = asyncio.Queue(maxsize=DATA_QUEUE_LIMIT)
        self._backend: SensorBackend = create_backend_instance(
            self.config['backend']['module'],
            self.config['backend'],
            SensorBackendQueue(self._data_queue))

        # webcam backend
        self._webcam: WebcamBackend | None = None
        self._webcam_callback: WebcamBackendCallback | None = None
        if 'webcam' in self.config:
            self._webcam_callback = WebcamBackendCallback()
            from .backend.tapocam import TapoWebcamBackend
            self._webcam = TapoWebcamBackend(self.config['webcam'], self._webcam_callback)

        # data upload frontend
        self._frontend: DataFrontend = HTTPDataFrontend(self.config['frontend'])

        # static dashboard generator
        self._dashboard: StaticDashboardGenerator | None = None
        if 'dashboard' in self.config:
            from .dashboard import StaticDashboardGenerator
            self._dashboard = StaticDashboardGenerator(self.config['dashboard'])

        self._shutdown_event = asyncio.Event()
        self._failed_data: deque[SensorData] = deque(maxlen=FAILED_QUEUE_LIMIT)
        self._dashboard_task: asyncio.Future | None = None

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

        # set up the data collection frontend
        await self._frontend.setup()

        # start collecting data
        collect_tasks = [asyncio.get_running_loop().create_task(self._collect_data_start())]
        if self._webcam:
            collect_tasks.append(asyncio.get_running_loop().create_task(self._collect_webcam_start()))

        # everything is set up!
        notify_ready()

        await self._shutdown_event.wait()

        # shutdown event was triggered, shutting down
        notify_stopping()

        # cleanup
        for task in collect_tasks:
            task.cancel()
        # return_exceptions=True - prevent raise of asyncio.CancelledError
        await asyncio.gather(*collect_tasks, return_exceptions=True)
        await self._backend.stop()
        if self._webcam:
            await self._webcam.stop()
        if self._dashboard_task:
            self._dashboard_task.cancel()
            # return_exceptions=True - prevent raise of asyncio.CancelledError
            await asyncio.gather(self._dashboard_task, return_exceptions=True)

    async def _collect_data_start(self):
        _LOGGER.debug("Starting data collection")

        while True:
            try:
                data: SensorData = await self._data_queue.get()
                try:
                    if self._dashboard:
                        # create background job for generating a static dashboard
                        if self._dashboard_task is None or self._dashboard_task.done():
                            self._dashboard_task = asyncio.create_task(self._generate_dashboard(data))
                        else:
                            # FIXME it shouldn't happen with proper timings, but it's not the proper way to handle this
                            _LOGGER.debug("Dashboard render still running, skipping this reading")

                    # we also send the data that failed during the previous attempt
                    await self._frontend.send_data([*self._failed_data, data])
                    self._failed_data.clear()
                except DataRejectedError:
                    _LOGGER.warning("Data was rejected by server, dropping %d readings",
                                    len(self._failed_data) + 1, exc_info=True)
                    self._failed_data.clear()
                except Exception:
                    # TODO proper exception handling
                    _LOGGER.warning("Failed to send data", exc_info=True)
                    # store the data for a later retry attempt
                    self._failed_data.append(data)
            except asyncio.CancelledError:
                # we've been canceled, shutting down
                break
            except Exception:
                _LOGGER.error("Unexpected error", exc_info=True)
                # TODO proper error handling

    async def _collect_webcam_start(self):
        _LOGGER.debug("Starting webcam data collection")

        while True:
            try:
                webcam_data = await self._webcam_callback.get_data()
                if webcam_data:
                    await self._frontend.send_webcam(webcam_data)
            except asyncio.CancelledError:
                # we've been canceled, shutting down
                break
            except Exception:
                # TODO proper exception handling
                _LOGGER.warning("Failed to send webcam data", exc_info=True)

    async def _generate_dashboard(self, data: SensorData):
        try:
            return await asyncio.to_thread(self._dashboard.generate_dashboard, data)
        except Exception:
            _LOGGER.error("Error generating dashboard", exc_info=True)


def notify_ready():
    if is_systemd():
        from . import sdnotify
        sdnotify.SystemdNotifier().notify("READY=1")


def notify_stopping():
    if is_systemd():
        from . import sdnotify
        sdnotify.SystemdNotifier().notify("STOPPING=1")


def is_systemd():
    return 'NOTIFY_SOCKET' in os.environ


def is_journal_enabled():
    return 'JOURNAL_STREAM' in os.environ


def main() -> None:
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
    # silence font subsetting noise from WeasyPrint
    logging.getLogger("fontTools.subset").setLevel(logging.WARNING)

    asyncio.run(WeatherDaemon(sys.argv).run())
