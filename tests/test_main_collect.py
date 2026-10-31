import asyncio
from contextlib import asynccontextmanager

from conftest import FakeSensorBackend

from metarstation_daemon.data import SensorData
from metarstation_daemon.frontend.interface import DataFrontend
from metarstation_daemon.main import WeatherDaemon

TEST_TIMEOUT_SECS = 5
"""Upper bound for the daemon to react in tests: hitting it means the collection loop is stuck."""

COLLECT_TIMEOUT_SECS = 0.3
"""Collection timeout used by the tests: long enough for an instant backend to answer well within it."""


class RecordingFrontend(DataFrontend):
    """Records the packets the daemon sends."""

    def __init__(self):
        super().__init__({})
        self.sent: list[SensorData] = []

    async def setup(self):
        pass

    async def send_data(self, data: list[SensorData]):
        self.sent.extend(data)

    async def send_webcam(self, data):
        pass


class NeverAnsweringBackend(FakeSensorBackend):
    """A device that never has anything to say, and remembers being given up on."""

    def __init__(self):
        super().__init__()
        self.cancelled = False

    async def get_data(self) -> SensorData | None:
        try:
            return await super().get_data()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


async def _collect_readings(daemon: WeatherDaemon, *backends) -> list[SensorData]:
    """Runs a single collection cycle."""
    # noinspection PyProtectedMember
    daemon._backends = list(backends)
    # noinspection PyProtectedMember
    daemon._collect_timeout_secs = COLLECT_TIMEOUT_SECS
    # noinspection PyProtectedMember
    return await daemon._collect_readings()


@asynccontextmanager
async def _collect_loop(daemon: WeatherDaemon, *backends):
    """Runs the data collection loop for the duration of the block, back to back."""
    frontend = RecordingFrontend()
    # noinspection PyProtectedMember
    daemon._frontend = frontend
    # noinspection PyProtectedMember
    daemon._backends = list(backends)
    # noinspection PyProtectedMember
    daemon._collect_interval_secs = 0
    # noinspection PyProtectedMember
    daemon._collect_timeout_secs = COLLECT_TIMEOUT_SECS
    # noinspection PyProtectedMember
    task = asyncio.create_task(daemon._collect_data_start())
    try:
        yield frontend
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_for_packet(frontend: RecordingFrontend) -> SensorData:
    async with asyncio.timeout(TEST_TIMEOUT_SECS):
        while not frontend.sent:
            await asyncio.sleep(0.01)
    return frontend.sent[0]


class TestCollectReadings:

    async def test_returns_every_reading_without_waiting_for_the_timeout(self, daemon):
        first, second = SensorData(temperature=1.0), SensorData(temperature=2.0)
        started = asyncio.get_running_loop().time()

        readings = await _collect_readings(daemon, FakeSensorBackend(first),
                                           FakeSensorBackend(second))

        assert readings == [first, second]
        assert asyncio.get_running_loop().time() - started < COLLECT_TIMEOUT_SECS

    async def test_readings_keep_the_configuration_order(self, daemon):
        """The merge gives the last backend precedence, so the order must not depend on timings."""
        slow, fast = SensorData(temperature=1.0), SensorData(temperature=2.0)

        readings = await _collect_readings(
            daemon,
            FakeSensorBackend(slow, delay_secs=COLLECT_TIMEOUT_SECS / 3),
            FakeSensorBackend(fast))

        assert readings == [slow, fast]

    async def test_a_backend_that_does_not_answer_in_time_is_left_out(self, daemon):
        reading = SensorData(temperature=1.0)
        slow = NeverAnsweringBackend()
        started = asyncio.get_running_loop().time()

        readings = await _collect_readings(daemon, FakeSensorBackend(reading), slow)

        assert readings == [reading]
        assert slow.cancelled
        assert asyncio.get_running_loop().time() - started >= COLLECT_TIMEOUT_SECS

    async def test_a_failing_backend_does_not_hold_back_the_others(self, daemon):
        reading = SensorData(temperature=1.0)

        readings = await _collect_readings(daemon,
                                           FakeSensorBackend(error=RuntimeError('device is on fire')),
                                           FakeSensorBackend(reading))

        assert readings == [reading]

    async def test_a_backend_with_nothing_to_give_is_left_out(self, daemon):
        reading = SensorData(temperature=1.0)

        readings = await _collect_readings(daemon, FakeSensorBackend(None),
                                           FakeSensorBackend(reading))

        assert readings == [reading]

    async def test_no_answer_at_all_gives_no_readings(self, daemon):
        readings = await _collect_readings(daemon, FakeSensorBackend(None), NeverAnsweringBackend())

        assert readings == []


class TestCollectLoop:

    async def test_sends_one_merged_packet_per_cycle(self, daemon):
        first = SensorData(temperature=1.0, battery={'ws90-aabbccddeeff': 80})
        second = SensorData(temperature=2.0, humidity=50.0, battery={'dummy': 100})

        async with _collect_loop(daemon, FakeSensorBackend(first), FakeSensorBackend(second)) as frontend:
            packet = await _wait_for_packet(frontend)

        # the last backend wins on a conflicting field, and both batteries are reported
        assert packet.temperature == 2.0
        assert packet.humidity == 50.0
        assert packet.battery == {'ws90-aabbccddeeff': 80, 'dummy': 100}

    async def test_a_partial_packet_is_sent_with_the_missing_fields_unavailable(self, daemon):
        reading = SensorData(temperature=1.0)

        async with _collect_loop(daemon, FakeSensorBackend(reading), NeverAnsweringBackend()) as frontend:
            packet = await _wait_for_packet(frontend)

        assert packet.temperature == 1.0
        # no last known value is reused: what nobody reported stays null
        assert packet.humidity is None
        assert packet.battery is None

    async def test_a_cycle_without_readings_sends_nothing(self, daemon):
        async with _collect_loop(daemon, NeverAnsweringBackend()) as frontend:
            # enough for a couple of cycles to come and go
            await asyncio.sleep(COLLECT_TIMEOUT_SECS * 2.5)

            assert frontend.sent == []
