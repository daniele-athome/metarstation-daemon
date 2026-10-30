import asyncio

import pytest

from metarstation_daemon.data import SensorData
from metarstation_daemon.frontend.interface import DataFrontend, DataFrontendError, DataRejectedError
from metarstation_daemon.main import WeatherDaemon

TEST_TIMEOUT_SECS = 5
"""Upper bound for the daemon to react in tests: hitting it means the data collection loop is stuck."""

CONFIG = """
[[backend]]
module = "dummy.DummySensorBackend"

[frontend]
data_url = "http://localhost/push"
image_url = "http://localhost/image"
api_token = "token"
"""


class FakeFrontend(DataFrontend):
    """Records every batch it is sent, failing with the given errors in order (None means success)."""

    def __init__(self, *errors: Exception | None):
        super().__init__({})
        self._errors = list(errors)
        self.batches: list[list[SensorData]] = []

    async def setup(self):
        pass

    async def send_data(self, data: list[SensorData]):
        self.batches.append(list(data))
        error = self._errors.pop(0) if self._errors else None
        if error:
            raise error

    async def send_webcam(self, data):
        pass


@pytest.fixture
def daemon(tmp_path) -> WeatherDaemon:
    config_file = tmp_path / 'config.toml'
    config_file.write_text(CONFIG)
    return WeatherDaemon(['weather-daemon', '-c', str(config_file)])


async def _collect(daemon: WeatherDaemon, frontend: FakeFrontend, readings: list[SensorData]):
    """Runs the data collection loop, feeding it one reading at a time."""
    # noinspection PyProtectedMember
    daemon._frontend = frontend
    # noinspection PyProtectedMember
    task = asyncio.create_task(daemon._collect_data_start())
    try:
        for i, reading in enumerate(readings, start=1):
            # noinspection PyProtectedMember
            daemon._data_queue.put_nowait(reading)
            async with asyncio.timeout(TEST_TIMEOUT_SECS):
                while len(frontend.batches) < i:
                    await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_sends_every_reading(daemon):
    frontend = FakeFrontend()
    first, second = SensorData(), SensorData()

    await _collect(daemon, frontend, [first, second])

    assert frontend.batches == [[first], [second]]


async def test_temporary_failure_sends_the_reading_again_later(daemon):
    frontend = FakeFrontend(DataFrontendError())
    first, second = SensorData(), SensorData()

    await _collect(daemon, frontend, [first, second])

    assert frontend.batches == [[first], [first, second]]


async def test_rejected_reading_is_dropped(daemon):
    frontend = FakeFrontend(DataRejectedError())
    first, second = SensorData(), SensorData()

    await _collect(daemon, frontend, [first, second])

    assert frontend.batches == [[first], [second]]


async def test_rejection_drops_the_whole_batch(daemon):
    """A rejected batch can't be told apart: keeping any of it could block every later reading."""
    frontend = FakeFrontend(DataFrontendError(), DataRejectedError())
    first, second, third = SensorData(), SensorData(), SensorData()

    await _collect(daemon, frontend, [first, second, third])

    assert frontend.batches == [[first], [first, second], [third]]
