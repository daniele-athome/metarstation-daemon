import datetime

import pytest

from metarstation_daemon.data import SensorData, WebcamData

TIMESTAMP = datetime.datetime(2026, 10, 19, 12, 30, 45, tzinfo=datetime.UTC)
"""A fixed reading timestamp, so that serialization is comparable."""


@pytest.fixture
def timestamp() -> datetime.datetime:
    return TIMESTAMP


@pytest.fixture
def sensor_data_full() -> SensorData:
    """A reading with every field populated, mirroring the dummy backend."""
    return SensorData(
        timestamp=TIMESTAMP,
        battery=100,
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
