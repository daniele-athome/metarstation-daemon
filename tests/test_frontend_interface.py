import pytest

from metarstation_daemon.frontend.interface import DataFrontend


class TestAbstractInterface:
    """The base class is a contract: a frontend that forgets to implement one must fail loudly."""

    async def test_setup_is_abstract(self):
        with pytest.raises(NotImplementedError):
            await DataFrontend({}).setup()

    async def test_send_data_is_abstract(self, sensor_data_full):
        with pytest.raises(NotImplementedError):
            await DataFrontend({}).send_data([sensor_data_full])

    async def test_send_webcam_is_abstract(self, webcam_data):
        with pytest.raises(NotImplementedError):
            await DataFrontend({}).send_webcam(webcam_data)
