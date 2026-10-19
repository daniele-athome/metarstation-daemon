import dataclasses
import datetime

import pytest

from metarstation_daemon.data import BATTERY_VALUE_AC, SensorData, WebcamData


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


def test_ac_battery_value_survives_serialization():
    data = SensorData(battery=BATTERY_VALUE_AC)

    assert data.to_dict()['battery'] == -1
    assert SensorData.from_dict(data.to_dict()).battery == BATTERY_VALUE_AC


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
