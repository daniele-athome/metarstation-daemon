import asyncio

from conftest import FakeSensorBackend

from metarstation_daemon.data import SensorData
from metarstation_daemon.frontend.interface import DataFrontend, DataFrontendError, DataRejectedError
from metarstation_daemon.main import WeatherDaemon

TEST_TIMEOUT_SECS = 5
"""Upper bound for the daemon to react in tests: hitting it means the data collection loop is stuck."""


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


def _reading(temperature: float) -> SensorData:
    """A reading that can be told apart from the others in a batch."""
    return SensorData(temperature=temperature)


def _temperatures(batches: list[list[SensorData]]) -> list[list[float]]:
    """Merged packets are new objects, so batches are compared by content."""
    return [[data.temperature for data in batch] for batch in batches]


async def _collect(daemon: WeatherDaemon, frontend: FakeFrontend, readings: list[SensorData]):
    """Runs the data collection loop, feeding it one reading per cycle."""
    # noinspection PyProtectedMember
    daemon._frontend = frontend
    # noinspection PyProtectedMember
    daemon._backends = [FakeSensorBackend(*readings)]
    # noinspection PyProtectedMember
    daemon._collect_interval_secs = 0
    # noinspection PyProtectedMember
    daemon._collect_timeout_secs = TEST_TIMEOUT_SECS
    # noinspection PyProtectedMember
    task = asyncio.create_task(daemon._collect_data_start())
    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECS):
            while len(frontend.batches) < len(readings):
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_sends_every_reading(daemon):
    frontend = FakeFrontend()

    await _collect(daemon, frontend, [_reading(1), _reading(2)])

    assert _temperatures(frontend.batches) == [[1], [2]]


async def test_temporary_failure_sends_the_reading_again_later(daemon):
    frontend = FakeFrontend(DataFrontendError())

    await _collect(daemon, frontend, [_reading(1), _reading(2)])

    assert _temperatures(frontend.batches) == [[1], [1, 2]]


async def test_rejected_reading_is_dropped(daemon):
    frontend = FakeFrontend(DataRejectedError())

    await _collect(daemon, frontend, [_reading(1), _reading(2)])

    assert _temperatures(frontend.batches) == [[1], [2]]


async def test_rejection_drops_the_whole_batch(daemon):
    """A rejected batch can't be told apart: keeping any of it could block every later reading."""
    frontend = FakeFrontend(DataFrontendError(), DataRejectedError())

    await _collect(daemon, frontend, [_reading(1), _reading(2), _reading(3)])

    assert _temperatures(frontend.batches) == [[1], [1, 2], [3]]
