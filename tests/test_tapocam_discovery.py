from kasa import DeviceType

from metarstation_daemon.backend.tapocam import find_first_camera_address


class FakeKasaDevice:
    def __init__(self, device_type: DeviceType):
        self.device_type = device_type


def test_finds_the_camera_among_other_devices():
    devices = {
        '192.168.1.10': FakeKasaDevice(DeviceType.Plug),
        '192.168.1.11': FakeKasaDevice(DeviceType.Camera),
        '192.168.1.12': FakeKasaDevice(DeviceType.Bulb),
    }

    assert find_first_camera_address(devices) == '192.168.1.11'


def test_takes_the_first_camera_in_discovery_order():
    devices = {
        '192.168.1.11': FakeKasaDevice(DeviceType.Camera),
        '192.168.1.12': FakeKasaDevice(DeviceType.Camera),
    }

    assert find_first_camera_address(devices) == '192.168.1.11'


def test_no_camera_found():
    devices = {'192.168.1.10': FakeKasaDevice(DeviceType.Plug)}

    assert find_first_camera_address(devices) is None


def test_nothing_discovered():
    assert find_first_camera_address({}) is None
