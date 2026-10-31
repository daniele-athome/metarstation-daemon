import dataclasses

from sensor_state_data import DeviceKey, SensorValue

from metarstation_daemon.backend.ws90 import WS90SensorBackend, _battery_key

BT_ADDRESS = '08:B9:5F:D4:2D:58'


def _sensor_value(key: str, value) -> SensorValue:
    return SensorValue(device_key=DeviceKey(key, None), name=key, native_value=value)


class TestBatteryKey:
    """The key is part of the contract with the collection server, so pin it down."""

    def test_colons_are_stripped_and_the_address_lowercased(self):
        assert _battery_key(BT_ADDRESS) == 'ws90-08b95fd42d58'

    def test_dashes_are_stripped_too(self):
        assert _battery_key('08-B9-5F-D4-2D-58') == 'ws90-08b95fd42d58'


class TestAddSensorValue:

    def test_battery_is_keyed_by_the_reporting_device(self):
        backend = WS90SensorBackend({})

        # noinspection PyProtectedMember
        backend._add_sensor_value(_sensor_value('battery', 80), BT_ADDRESS)

        # noinspection PyProtectedMember
        assert backend._latest_data.battery == {'ws90-08b95fd42d58': 80}

    def test_a_mapped_value_reaches_its_attribute(self):
        backend = WS90SensorBackend({})

        # noinspection PyProtectedMember
        backend._add_sensor_value(_sensor_value('speed_1', 3.5), BT_ADDRESS)

        # noinspection PyProtectedMember
        assert backend._latest_data.wind_speed == 3.5

    def test_an_unknown_value_is_ignored(self):
        backend = WS90SensorBackend({})
        # noinspection PyProtectedMember
        untouched = dataclasses.replace(backend._latest_data)

        # noinspection PyProtectedMember
        backend._add_sensor_value(_sensor_value('nope', 1), BT_ADDRESS)

        # noinspection PyProtectedMember
        assert backend._latest_data == untouched


def test_reset_clears_the_data_event():
    """Every get_data call must start from a clean event, or it would return a stale packet."""
    backend = WS90SensorBackend({})
    # noinspection PyProtectedMember
    backend._data_event.set()

    # noinspection PyProtectedMember
    backend._reset_sensor_data()

    # noinspection PyProtectedMember
    assert not backend._data_event.is_set()
