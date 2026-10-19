# METAR Station daemon

This software is part of a suite for running a weather station for an airfield/airstrip.

This is the service that runs in some computer at the airfield and collects data from some weather device. Data is
periodically uploaded to some server.

At the moment only the following is supported:

* [Ecowitt WS90 Shelly-based weather station](https://shelly-api-docs.shelly.cloud/docs-ble/Devices/BLU_ZB/wstation/)
* Some Tapo cameras (only tested with [Tapo C500](https://www.tapo.com/en/product/smart-camera/tapo-c500/))
* Sending data to a HTTP endpoint supporting bearer token authentication (as in `Authorization: Bearer ...`)

## Non-Python Dependencies

Must be available in your `PATH`:

* `ffmpeg` if you enable the webcam integration section in configuration
* `pdftoppm` if you enable the dashboard section in configuration

## Configure

An example configuration file is in `config.toml.example`.

## Run

Running the program requires either [uv](https://docs.astral.sh/uv/) or installing into a venv:

```shell
# Faster: run with uv
uv run weather-daemon -c config.toml
# Annoying: create and install inside of a venv
python3 -m venv .venv
source .venv/bin/activate
pip install .
weather-daemon -c config.toml
```
