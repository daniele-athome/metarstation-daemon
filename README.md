# METAR Station daemon

This software is part of a suite for running a weather station for an airfield/airstrip.

This is the service that runs in some computer at the airfield and collects data from some weather device. Data is
periodically uploaded to some server.

At the moment only the following is supported:

* [Ecowitt WS90 Shelly-based weather station](https://shelly-api-docs.shelly.cloud/docs-ble/Devices/BLU_ZB/wstation/)
* Sending data to a HTTP endpoint supporting bearer token authentication (as in `Authorization: Bearer ...`)

## Configure

Configuration is in `config.toml`.

## Run

Running the program requires `uv` to be installed: https://docs.astral.sh/uv/

```shell
./weather-daemon.py -c config.toml
```
