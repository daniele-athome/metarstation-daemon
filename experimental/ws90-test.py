#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak", "bthome-ble"]
# ///

import asyncio
from uuid import UUID

from bleak import BleakScanner, BLEDevice, AdvertisementData
from bleak.assigned_numbers import AdvertisementDataType
from bthome_ble import BTHomeBluetoothDeviceData
from habluetooth.models import BluetoothServiceInfoBleak
from bluetooth_data_tools import monotonic_time_coarse
from habluetooth.scanner import BlueZScannerArgs, OrPattern

UUID_WS90 = '6720fc43-27ed-4c02-ac27-e4ea85b5bcfd'

filter_settings: BlueZScannerArgs = BlueZScannerArgs(
    or_patterns = [OrPattern(0, AdvertisementDataType.SERVICE_DATA_UUID32,
                             UUID(UUID_WS90).bytes)]
)

async def main():
    def callback(device: BLEDevice, advertisement_data: AdvertisementData):
        print(f"Device: {device.address}")
        if device.address != '08:B9:5F:D4:2D:58':
            return

        if advertisement_data:
            print("Decoded data:", advertisement_data)

        service_info = (BluetoothServiceInfoBleak
                        .from_device_and_advertisement_data(device, advertisement_data,
                                                            "local", monotonic_time_coarse(), True))

        device_data = BTHomeBluetoothDeviceData()
        result = device_data.update(service_info)
        if result:
            print(f"Parsed!")
            print(device_data._sensor_descriptions)
            print(device_data._sensor_values)
        else:
            print(f"Failed!")

    scanner = BleakScanner(callback,
                           scanning_mode='active',
                           service_uuids=[UUID_WS90],
                           #bluez=filter_settings,
                           )
    await scanner.start()
    await asyncio.sleep(60)
    await scanner.stop()

asyncio.run(main())
