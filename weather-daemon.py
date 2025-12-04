#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak", "bthome-ble"]
# ///

import asyncio
import logging
import sys

from weather_test import WeatherDaemon

if __name__ == '__main__':
    formatter = "[%(asctime)s] %(name)s {%(filename)s:%(lineno)d} %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=formatter)

    asyncio.run(WeatherDaemon(sys.argv).run())
