import dataclasses
import datetime

import pytest

from metarstation_daemon.data import SensorData, WebcamData, merge_sensor_data


def test_defaults_are_unavailable():
    data = SensorData()

    for f in dataclasses.fields(data):
        if f.name != 'timestamp':
            assert getattr(data, f.name) is None, f"{f.name} should default to unavailable"


def test_default_timestamp_is_utc_aware():
    data = SensorData()

    # a naive timestamp would be silently misinterpreted when converted to local time
    assert data.timestamp.tzinfo is datetime.UTC


def test_fields_are_keyword_only(timestamp):
    with pytest.raises(TypeError):
        # noinspection PyArgumentList
        SensorData(timestamp)


def test_timestamp_is_serialized_as_iso8601_with_offset(sensor_data_empty):
    # this is part of the contract with the collection server, so pin it down
    assert sensor_data_empty.to_dict()['timestamp'] == '2026-10-19T12:30:45+00:00'


def test_to_dict_exposes_every_field(sensor_data_full):
    expected_keys = {f.name for f in dataclasses.fields(SensorData)}

    # a renamed field would silently stop reaching the server, so compare the whole key set
    assert sensor_data_full.to_dict().keys() == expected_keys


def test_roundtrip_preserves_a_full_reading(sensor_data_full):
    assert SensorData.from_dict(sensor_data_full.to_dict()) == sensor_data_full


def test_roundtrip_preserves_an_empty_reading(sensor_data_empty):
    assert SensorData.from_dict(sensor_data_empty.to_dict()) == sensor_data_empty


def test_unavailable_values_are_serialized_as_null(sensor_data_empty):
    serialized = sensor_data_empty.to_dict()

    assert all(value is None for key, value in serialized.items() if key != 'timestamp')


class TestMergeSensorData:
    """Readings from several backends become the single packet sent to the server."""

    def test_the_last_reading_wins(self, timestamp):
        merged = merge_sensor_data([SensorData(temperature=1.0), SensorData(temperature=2.0)],
                                   timestamp)

        assert merged.temperature == 2.0

    def test_an_unavailable_value_keeps_the_previous_one(self, timestamp):
        merged = merge_sensor_data([SensorData(temperature=1.0), SensorData(humidity=50.0)],
                                   timestamp)

        assert merged.temperature == 1.0
        assert merged.humidity == 50.0

    def test_missing_values_stay_unavailable(self, timestamp):
        merged = merge_sensor_data([SensorData(temperature=1.0)], timestamp)

        assert merged.humidity is None

    def test_every_field_is_merged(self, sensor_data_full, sensor_data_empty, timestamp):
        merged = merge_sensor_data([sensor_data_empty, sensor_data_full], timestamp)

        # a field added to SensorData must be merged without touching merge_sensor_data
        assert merged == sensor_data_full

    def test_no_readings_give_an_empty_packet(self, timestamp):
        merged = merge_sensor_data([], timestamp)

        for f in dataclasses.fields(merged):
            if f.name != 'timestamp':
                assert getattr(merged, f.name) is None

    def test_battery_keys_are_merged(self, timestamp):
        merged = merge_sensor_data([SensorData(battery={'ws90-aabbccddeeff': 80}),
                                    SensorData(battery={'dummy': 100})], timestamp)

        assert merged.battery == {'ws90-aabbccddeeff': 80, 'dummy': 100}

    def test_the_last_battery_wins_on_a_duplicate_key(self, timestamp):
        merged = merge_sensor_data([SensorData(battery={'dummy': 80}),
                                    SensorData(battery={'dummy': 100})], timestamp)

        assert merged.battery == {'dummy': 100}

    def test_battery_is_unavailable_when_nobody_reported_it(self, timestamp):
        merged = merge_sensor_data([SensorData(temperature=1.0)], timestamp)

        # null in the JSON, like any other unavailable field
        assert merged.battery is None

    def test_the_caller_timestamp_wins_over_the_readings(self, timestamp):
        collected_at = timestamp + datetime.timedelta(minutes=5)

        merged = merge_sensor_data([SensorData(timestamp=timestamp, temperature=1.0)], collected_at)

        assert merged.timestamp == collected_at


def test_webcam_data_requires_every_field():
    with pytest.raises(TypeError):
        # noinspection PyArgumentList
        WebcamData(image_data=b'', image_type='image/jpeg')


def test_webcam_data_holds_the_snapshot(timestamp):
    expire = timestamp + datetime.timedelta(seconds=60)
    data = WebcamData(expire=expire, image_data=b'\xff\xd8\xff', image_type='image/jpeg')

    assert data.expire == expire
    assert data.image_data == b'\xff\xd8\xff'
    assert data.image_type == 'image/jpeg'
