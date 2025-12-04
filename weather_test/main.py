import asyncio

from weather_test.backends.ws90 import WS90SensorBackend


class WeatherDaemon:
    def __init__(self, args):
        pass

    async def run(self):
        # TEST
        b = WS90SensorBackend({'bt_address': '08:B9:5F:D4:2D:58'})
        await b.start()
        await asyncio.sleep(300)
        await b.stop()
