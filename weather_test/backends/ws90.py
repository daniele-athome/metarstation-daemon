from bleak import BleakScanner, BLEDevice, AdvertisementData

from .interface import SensorBackend
from ..data import SensorData


SERVICE_DATA_UUID = '6720fc43-27ed-4c02-ac27-e4ea85b5bcfd'
"""
Service data UUID for the Ecowitt WS90 weather station.
https://shelly-api-docs.shelly.cloud/docs-ble/Devices/BLU_ZB/wstation/
"""


class WS90SensorBackend(SensorBackend):

    def __init__(self, config):
        super().__init__(config)
        self.bt_address: str = config['bt_address']
        self._scanner = BleakScanner(self._callback, scanning_mode='active', service_uuids=[SERVICE_DATA_UUID])

    async def start(self):
        await self._scanner.start()

    async def stop(self):
        await self._scanner.stop()

    async def get_data(self) -> list[SensorData]:
        # TODO
        return []

    def _callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        print(f"Device: {device.address}")
        if device.address != self.bt_address:
            return

        # TODO process data
        pass
