import dataclasses
import datetime


BATTERY_VALUE_AC = -1
"""Battery value when AC is connected."""


@dataclasses.dataclass(kw_only=True)
class SensorData:

    timestamp: datetime.datetime = datetime.datetime.now(datetime.UTC)
    """Sensor reading timestamp."""

    battery: int|None = -1
    """Battery level (%). Use None for data unavailable, BATTERY_AC when on AC power."""

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
