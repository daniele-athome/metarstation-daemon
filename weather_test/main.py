import asyncio
import functools
import logging
import signal

from weather_test.backends.ws90 import WS90SensorBackend

_LOGGER = logging.getLogger(__name__)


class WeatherDaemon:
    def __init__(self, args):
        self.shutdown_event = asyncio.Event()

    async def run(self):
        def sig_handler(code):
            _LOGGER.info("Received signal %s", code)
            self.shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, functools.partial(sig_handler, sig))

        # TEST
        b = WS90SensorBackend({'bt_address': '08:B9:5F:D4:2D:58'})
        await b.start()

        await self.shutdown_event.wait()

        await b.stop()

def main(args):
    # TODO proper logging configuration
    formatter = "[%(asctime)s] %(name)s %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=formatter)
    logging.getLogger(__package__).setLevel(logging.DEBUG)

    asyncio.run(WeatherDaemon(args).run())
