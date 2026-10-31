import logging
from pathlib import Path

from .interface import SensorBackend
from ..data import SensorData

SYSFS_PATH = '/sys/class/power_supply'
SYSFS_DEFAULT_BATTERY = 'BAT0'
SYSFS_DEFAULT_AC = 'AC'

# battery dict keys for the final SensorData object
BATTERY_KEY = 'sys-battery'
AC_KEY = 'sys-ac'

# symbolic values for AC (100=connected, 0=disconnected)
AC_CONNECTED = 100
AC_DISCONNECTED = 0

_LOGGER = logging.getLogger(__name__)


class SystemPowerBackend(SensorBackend):
    """Power supply status of the machine running the daemon, read from the sysfs power_supply class."""

    def __init__(self, config):
        super().__init__(config)
        sysfs_path = Path(SYSFS_PATH)
        battery_device = config.get('battery_device', SYSFS_DEFAULT_BATTERY)
        ac_device = config.get('ac_device', SYSFS_DEFAULT_AC)
        self._capacity_path = sysfs_path / battery_device / 'capacity' if battery_device else None
        self._online_path = sysfs_path / ac_device / 'online' if ac_device else None
        self._unreadable: set[Path] = set()

    async def start(self):
        _LOGGER.info("System power sensor starting")
        for path in (self._capacity_path, self._online_path):
            if path:
                _LOGGER.debug("System power reading %s (readable: %s)", path, path.is_file())

    async def stop(self):
        _LOGGER.info("System power sensor stopping")

    async def get_data(self) -> SensorData|None:
        battery: dict[str, int] = {}

        capacity = self._read_value(self._capacity_path)
        if capacity is not None:
            # a broken driver may report outside of the documented 0-100 range
            battery[BATTERY_KEY] = min(max(capacity, 0), 100)

        online = self._read_value(self._online_path)
        if online is not None:
            battery[AC_KEY] = AC_CONNECTED if online else AC_DISCONNECTED

        return SensorData(battery=battery) if battery else None

    def _read_value(self, path: Path|None) -> int|None:
        if not path:
            return None

        try:
            value = int(path.read_text().strip())
        except (OSError, ValueError):
            # log the first failure only: a missing device would flood the log otherwise
            if path not in self._unreadable:
                self._unreadable.add(path)
                _LOGGER.warning("Unable to read power supply value from %s", path, exc_info=True)
            return None

        self._unreadable.discard(path)
        return value
