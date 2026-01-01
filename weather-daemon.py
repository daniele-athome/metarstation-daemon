#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak", "bthome-ble", "httpx", "dataclasses-json"]
# ///

import sys

import metarstation_daemon

if __name__ == '__main__':
    metarstation_daemon.main(sys.argv)
