import datetime
import logging

from bleak import BleakScanner, BLEDevice, AdvertisementData
from bluetooth_data_tools import monotonic_time_coarse
from bthome_ble import BTHomeBluetoothDeviceData
from habluetooth import BluetoothServiceInfoBleak
from sensor_state_data import SensorValue, DeviceKey

from .interface import SensorBackend, SensorBackendQueue
from ..data import SensorData

SERVICE_DATA_UUID = '6720fc43-27ed-4c02-ac27-e4ea85b5bcfd'
"""
Service data UUID for the Ecowitt WS90 weather station.
https://shelly-api-docs.shelly.cloud/docs-ble/Devices/BLU_ZB/wstation/
"""

_LOGGER = logging.getLogger(__name__)


def _is_packet1(data: BTHomeBluetoothDeviceData) -> bool:
    return DeviceKey('illuminance', None) in data._sensor_values


def _is_packet2(data: BTHomeBluetoothDeviceData) -> bool:
    return DeviceKey('battery', None) in data._sensor_values


DATA_MAPPING = {
    'battery': ('battery', int),
    'temperature': ('temperature', float),
    'humidity': ('humidity', float),
    'dew_point': ('dew_point', float),
    'pressure': ('pressure', float),
    'illuminance': ('illumination', float),
    'speed_1': ('wind_speed', float),
    'speed_2': ('gust_speed', float),
    'direction': ('wind_direction', int),
    'uv_index': ('uv_index', int),
    'precipitation': ('precipitation', float),
}


class WS90SensorBackend(SensorBackend):

    def __init__(self, config, queue: SensorBackendQueue):
        super().__init__(config, queue)
        self.bt_address: str = config['bt_address']
        # TODO passive scan doesn't work without some tricks; it's probably better to just use active scan at regular intervals anyway
        self._scanner = BleakScanner(self._callback,
                                     scanning_mode='active',
                                     service_uuids=[SERVICE_DATA_UUID])
        self._packet1_received = False
        self._packet2_received = False
        self._latest_data = SensorData()

    async def start(self):
        _LOGGER.debug(f"WS90 scanner for {self.bt_address} starting")
        await self._scanner.start()

    async def stop(self):
        _LOGGER.debug("WS90 scanner stopping")
        await self._scanner.stop()

    def _push_sensor_value(self):
        self._latest_data.timestamp = datetime.datetime.now(datetime.UTC)
        self.queue.push(self._latest_data)
        # reset buffer object
        self._latest_data = SensorData()
        self._packet1_received = False
        self._packet2_received = False

    def _add_sensor_value(self, value: SensorValue):
        sensor_id = value.device_key.key
        if sensor_id in DATA_MAPPING:
            prop_name, converter = DATA_MAPPING[sensor_id]
            setattr(self._latest_data, prop_name, converter(value.native_value))

    def _callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        _LOGGER.debug(f"Device: {device.address}")
        if device.address != self.bt_address:
            _LOGGER.debug(f"Not our device, discarding advertisement")
            return

        if not advertisement_data:
            _LOGGER.warning("No advertisement data")
            return

        service_info = (BluetoothServiceInfoBleak
                        .from_device_and_advertisement_data(device, advertisement_data,
                                                            "local", monotonic_time_coarse(), True))

        device_data = BTHomeBluetoothDeviceData()
        if device_data.update(service_info):
            _LOGGER.debug(f"Advertisement data: {device_data._sensor_values}")
            for sensor_value in device_data._sensor_values.values():
                self._add_sensor_value(sensor_value)

            if _is_packet1(device_data):
                self._packet1_received = True
            elif _is_packet2(device_data):
                self._packet2_received = True

            if self._packet1_received and self._packet2_received:
                _LOGGER.info(f"Latest data: {self._latest_data}")
                self._push_sensor_value()
