#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak", "bthome-ble"]
# ///

import sys

from weather_test import WeatherDaemon

if __name__ == '__main__':
    WeatherDaemon(sys.argv).run()
