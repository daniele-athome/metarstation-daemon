import asyncio
import datetime
from collections import deque

import pytest

from metarstation_daemon.backend.interface import SensorBackend
from metarstation_daemon.data import SensorData, WebcamData
from metarstation_daemon.main import WeatherDaemon

TIMESTAMP = datetime.datetime(2026, 10, 19, 12, 30, 45, tzinfo=datetime.UTC)
"""A fixed reading timestamp, so that serialization is comparable."""

DAEMON_CONFIG = """
[[backend]]
module = "dummy.DummySensorBackend"

[frontend]
data_url = "http://localhost/push"
image_url = "http://localhost/image"
api_token = "token"
"""


@pytest.fixture
def timestamp() -> datetime.datetime:
    return TIMESTAMP


@pytest.fixture
def sensor_data_full() -> SensorData:
    """A reading with every field populated, mirroring the dummy backend."""
    return SensorData(
        timestamp=TIMESTAMP,
        battery={'dummy': 100},
        temperature=20.2,
        humidity=64,
        dew_point=13.16,
        pressure=997.6,
        illumination=0,
        wind_speed=3,
        gust_speed=0,
        wind_direction=45,
        uv_index=0,
        raining=False,
        precipitation=331.4,
    )


@pytest.fixture
def sensor_data_empty() -> SensorData:
    """A reading where the sensor gave us nothing at all."""
    return SensorData(timestamp=TIMESTAMP)


@pytest.fixture
def webcam_data() -> WebcamData:
    return WebcamData(
        expire=TIMESTAMP + datetime.timedelta(seconds=60),
        image_data=b'\xff\xd8\xff\xd9',
        image_type='image/jpeg',
    )


@pytest.fixture
def daemon(tmp_path) -> WeatherDaemon:
    """A daemon whose backends and frontend are meant to be replaced by the test."""
    config_file = tmp_path / 'config.toml'
    config_file.write_text(DAEMON_CONFIG)
    return WeatherDaemon(['weather-daemon', '-c', str(config_file)])


class FakeSensorBackend(SensorBackend):
    """Answers with the given readings, one per call, then blocks like a device with nothing to say."""

    def __init__(self, *readings: SensorData | None,
                 delay_secs: float = 0, error: Exception | None = None):
        super().__init__({})
        self._readings = deque(readings)
        self._delay_secs = delay_secs
        self._error = error

    async def start(self):
        pass

    async def stop(self):
        pass

    async def get_data(self) -> SensorData | None:
        if self._delay_secs:
            await asyncio.sleep(self._delay_secs)
        if self._error:
            raise self._error
        if not self._readings:
            await asyncio.Event().wait()
        return self._readings.popleft()
