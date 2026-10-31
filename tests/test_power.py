import dataclasses
import logging

import pytest

from metarstation_daemon.backend.power import AC_KEY, BATTERY_KEY, SystemPowerBackend

NO_DEVICE = ''
"""Config value that opts out of one of the two devices, both being configured by default."""


class FakeSysfs:
    """A fake power supply class root the test populates device by device."""

    def __init__(self, root):
        self.root = root

    def write(self, device: str, attribute: str, value: str):
        (self.root / device).mkdir(exist_ok=True)
        (self.root / device / attribute).write_text(value)

    def remove(self, device: str, attribute: str):
        (self.root / device / attribute).unlink()

    def backend(self, **config) -> SystemPowerBackend:
        return SystemPowerBackend(config)


@pytest.fixture
def sysfs(tmp_path, monkeypatch) -> FakeSysfs:
    """A fake power supply class root, taking the place of /sys/class/power_supply.

    The backend resolves the device paths in its constructor, so the root is patched before the
    test builds one through FakeSysfs.backend().
    """
    monkeypatch.setattr('metarstation_daemon.backend.power.SYSFS_PATH', str(tmp_path))
    return FakeSysfs(tmp_path)


class TestGetData:

    async def test_reads_the_default_devices(self, sysfs):
        sysfs.write('BAT0', 'capacity', '99\n')
        sysfs.write('AC', 'online', '1\n')

        data = await sysfs.backend().get_data()

        assert data.battery == {BATTERY_KEY: 99, AC_KEY: 100}

    async def test_reports_the_configured_battery(self, sysfs):
        sysfs.write('BATT', 'capacity', '99\n')

        data = await sysfs.backend(battery_device='BATT', ac_device=NO_DEVICE).get_data()

        assert data.battery == {BATTERY_KEY: 99}

    async def test_connected_ac_is_reported_as_full(self, sysfs):
        sysfs.write('ADP1', 'online', '1\n')

        data = await sysfs.backend(battery_device=NO_DEVICE, ac_device='ADP1').get_data()

        assert data.battery == {AC_KEY: 100}

    async def test_disconnected_ac_is_reported_as_empty(self, sysfs):
        sysfs.write('AC', 'online', '0\n')

        data = await sysfs.backend(battery_device=NO_DEVICE).get_data()

        assert data.battery == {AC_KEY: 0}

    async def test_reports_nothing_but_the_battery(self, sysfs):
        """The whole point of this backend: it only contributes the battery dict to the packet."""
        sysfs.write('BAT0', 'capacity', '42\n')
        sysfs.write('AC', 'online', '1\n')

        data = await sysfs.backend().get_data()

        for f in dataclasses.fields(data):
            if f.name not in ('timestamp', 'battery'):
                assert getattr(data, f.name) is None, f"{f.name} should not be reported"

    async def test_a_device_can_be_opted_out(self, sysfs):
        sysfs.write('BAT0', 'capacity', '42\n')
        sysfs.write('AC', 'online', '1\n')

        data = await sysfs.backend(ac_device=NO_DEVICE).get_data()

        assert data.battery == {BATTERY_KEY: 42}

    async def test_whitespace_around_the_value_is_ignored(self, sysfs):
        sysfs.write('BAT0', 'capacity', ' 42 \n')

        data = await sysfs.backend(ac_device=NO_DEVICE).get_data()

        assert data.battery == {BATTERY_KEY: 42}

    @pytest.mark.parametrize('value, expected', [('105', 100), ('-5', 0)])
    async def test_a_capacity_out_of_range_is_clamped(self, sysfs, value, expected):
        sysfs.write('BAT0', 'capacity', value)

        data = await sysfs.backend(ac_device=NO_DEVICE).get_data()

        assert data.battery == {BATTERY_KEY: expected}

    async def test_no_device_configured_gives_nothing(self, sysfs):
        backend = sysfs.backend(battery_device=NO_DEVICE, ac_device=NO_DEVICE)

        assert await backend.get_data() is None

    async def test_a_missing_device_is_left_out(self, sysfs):
        sysfs.write('AC', 'online', '1\n')

        data = await sysfs.backend(battery_device='NOPE').get_data()

        assert data.battery == {AC_KEY: 100}

    async def test_a_missing_attribute_is_left_out(self, sysfs):
        # the device directory exists, but the driver doesn't expose the value
        sysfs.write('BAT0', 'status', 'Discharging\n')
        sysfs.write('AC', 'online', '1\n')

        data = await sysfs.backend().get_data()

        assert data.battery == {AC_KEY: 100}

    @pytest.mark.parametrize('value', ['', 'abc', 'Discharging'])
    async def test_a_non_numeric_value_is_left_out(self, sysfs, value):
        sysfs.write('BAT0', 'capacity', value)
        sysfs.write('AC', 'online', '1\n')

        data = await sysfs.backend().get_data()

        assert data.battery == {AC_KEY: 100}

    async def test_nothing_readable_gives_nothing(self, sysfs):
        data = await sysfs.backend().get_data()

        assert data is None


class TestFailureLogging:
    """With one cycle every few minutes, a missing device must not fill the log with warnings."""

    async def test_an_unreadable_device_is_logged_once(self, sysfs, caplog):
        backend = sysfs.backend(battery_device='NOPE', ac_device=NO_DEVICE)

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                await backend.get_data()

        assert len(caplog.records) == 1

    async def test_every_unreadable_device_is_logged(self, sysfs, caplog):
        backend = sysfs.backend()

        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                await backend.get_data()

        # one for the battery, one for the AC: both configured by default, neither present
        assert len(caplog.records) == 2

    async def test_the_failure_is_logged_again_after_a_recovery(self, sysfs, caplog):
        backend = sysfs.backend(ac_device=NO_DEVICE)

        with caplog.at_level(logging.WARNING):
            await backend.get_data()
            sysfs.write('BAT0', 'capacity', '42\n')
            await backend.get_data()
            sysfs.remove('BAT0', 'capacity')
            await backend.get_data()

        assert len(caplog.records) == 2
