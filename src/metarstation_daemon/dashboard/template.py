from dataclasses import dataclass, field

from jinja2 import Environment, PackageLoader, StrictUndefined

STRING_DASH = "--"


@dataclass
class Wind:
    speed: str | None = None  # "14"
    gust: str | None = None  # "26"
    dir_text: str | None = None  # "da 270° · O"
    rotation: int | None = None  # degrees; None => arrow not displayed


@dataclass
class Runway:
    rotation: int = 140  # runway QFU, from configuration


@dataclass
class Ephem:
    sunrise: str | None = None
    sunset: str | None = None
    twilight: str | None = None
    daylight: str | None = None


@dataclass
class Stamp:
    observed: str = None
    updated: str = None


@dataclass
class Board:
    site: str = ""
    date: str = ""
    clock: str = ""
    runway: Runway = field(default_factory=Runway)
    ephem: Ephem = field(default_factory=Ephem)
    stamp: Stamp = field(default_factory=Stamp)

    verdict: str | None = None
    sky: str | None = None
    temp: str | None = None
    qnh: str | None = None
    dew: str | None = None
    rh: str | None = None
    clouds: str | None = None
    vis: str | None = None
    wind: Wind = field(default_factory=Wind)


def _jinja_filter_dash(value):
    return "--" if value is None or value == "" else value


def _jinja_filter_dash_time(value):
    return "--:--" if value is None or value == "" else value


def _build_jinja_env() -> Environment:
    env = Environment(
        loader=PackageLoader(__name__, "templates"),
        undefined=StrictUndefined,
        autoescape=True,
    )
    env.filters["dash"] = _jinja_filter_dash
    env.filters["dash_time"] = _jinja_filter_dash_time
    return env


def load_template(self):
    # hard-coded template file for now :P
    return _build_jinja_env().get_template("dashboard_it.800x600.html.j2")
