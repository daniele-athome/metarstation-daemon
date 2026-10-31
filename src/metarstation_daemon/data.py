import datetime
from collections.abc import Iterable
from dataclasses import dataclass, field, fields

from mashumaro import DataClassDictMixin


@dataclass(kw_only=True)
class SensorData(DataClassDictMixin):

    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC),
    )
    """Sensor reading timestamp."""

    battery: dict[str, int]|None = None
    """Battery level (%) per device, keyed by the reporting device. Use None for data unavailable."""

    temperature: float|None = None
    """Temperature in degrees celsius."""

    humidity: float|None = None
    """Relative humidity (%)."""

    dew_point: float|None = None
    """Dew point in degrees celsius."""

    pressure: float|None = None
    """Atmospheric pressure (hPa)."""

    illumination: float|None = None
    """Illumination level (lux)."""

    wind_speed: float|None = None
    """Wind speed (m/s)."""

    gust_speed: float|None = None
    """Gust speed (m/s)."""

    wind_direction: int|None = None
    """Wind direction (degrees)."""

    uv_index: int|None = None
    """UV index."""

    raining: bool|None = None
    """Is it raining?"""

    precipitation: float|None = None
    """Precipitation (mm/h)."""


_MERGE_SKIP = ('timestamp', 'battery')
"""Fields of SensorData that merge_sensor_data handles on its own."""


def merge_sensor_data(readings: Iterable[SensorData],
                      timestamp: datetime.datetime) -> SensorData:
    """Merges readings in configuration order: the last non-None value wins."""
    merged = SensorData(timestamp=timestamp)
    battery: dict[str, int] = {}
    for reading in readings:
        battery |= reading.battery or {}
        for data_field in fields(SensorData):
            if data_field.name in _MERGE_SKIP:
                continue
            value = getattr(reading, data_field.name)
            if value is not None:
                setattr(merged, data_field.name, value)
    # keep it null in the JSON when no device reported a battery, like any other field
    merged.battery = battery or None
    return merged


@dataclass(kw_only=True)
class WebcamData:

    expire: datetime.datetime
    """Webcam snapshot expiration."""

    image_data: bytes
    """Snapshot image data."""

    image_type: str
    """Image MIME type."""
