import argparse
import asyncio
import datetime
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
from .backend.interface import SensorBackend, WebcamBackend, WebcamBackendCallback
from .data import SensorData, merge_sensor_data
from .frontend.http import HTTPDataFrontend
from .frontend.interface import DataFrontend, DataRejectedError

if TYPE_CHECKING:
    from .backend.tapocam import TapoWebcamBackend
    from .dashboard import StaticDashboardGenerator


_LOGGER = logging.getLogger(__name__)

COLLECT_INTERVAL_SECS = 60
"""Default delay between two data collection cycles."""

COLLECT_TIMEOUT_SECS = 30
"""Default time a data collection cycle waits for the sensor backends."""

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

        # sensor backends
        collect_config = self.config.get('collector', {})
        self._collect_interval_secs: int = collect_config.get('interval_secs', COLLECT_INTERVAL_SECS)
        self._collect_timeout_secs: int = collect_config.get('timeout_secs', COLLECT_TIMEOUT_SECS)
        self._backends: list[SensorBackend] = [
            create_backend_instance(backend_config['module'], backend_config)
            for backend_config in self.config['backend']
        ]

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

        # start collecting data from the devices
        for backend in self._backends:
            await backend.start()

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
        for backend in self._backends:
            await backend.stop()
        if self._webcam:
            await self._webcam.stop()
        if self._dashboard_task:
            self._dashboard_task.cancel()
            # return_exceptions=True - prevent raise of asyncio.CancelledError
            await asyncio.gather(self._dashboard_task, return_exceptions=True)

    async def _collect_readings(self) -> list[SensorData]:
        tasks = [asyncio.create_task(backend.get_data()) for backend in self._backends]
        try:
            # returns as soon as every backend answered, or when the timeout expires
            await asyncio.wait(tasks, timeout=self._collect_timeout_secs)
        finally:
            for task in tasks:
                task.cancel()
            # return_exceptions=True - prevent raise of asyncio.CancelledError
            await asyncio.gather(*tasks, return_exceptions=True)

        # we need the backend order (for merging the values), so iterate over the task list
        readings = []
        for backend, task in zip(self._backends, tasks):
            backend_name = type(backend).__name__
            if task.cancelled():
                _LOGGER.warning("Backend %s did not provide data in time", backend_name)
                continue
            error = task.exception()
            if error:
                _LOGGER.warning("Backend %s failed to provide data", backend_name, exc_info=error)
                continue
            data = task.result()
            if data:
                readings.append(data)
        return readings

    async def _collect_data_start(self):
        _LOGGER.debug("Starting data collection")

        while True:
            try:
                readings = await self._collect_readings()
                if readings:
                    data = merge_sensor_data(readings, datetime.datetime.now(datetime.UTC))
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
                else:
                    _LOGGER.debug("No sensor data collected, skipping this cycle")

                await asyncio.sleep(self._collect_interval_secs)
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
