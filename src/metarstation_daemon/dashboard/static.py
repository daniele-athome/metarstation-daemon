import datetime
import io
import logging
import math
import os
import subprocess
import tempfile
from dataclasses import asdict
from zoneinfo import ZoneInfo

from PIL import Image
from weasyprint import HTML

from .template import load_template, Board, Ephem, Wind, Stamp, Runway
from ..data import SensorData

_LOGGER = logging.getLogger(__name__)

# maps clockwise rotation angle to PIL rotation constants
_ROTATE_TRANSPOSE = {
    90: Image.ROTATE_270,
    180: Image.ROTATE_180,
    270: Image.ROTATE_90,
}

# date names are hard-coded to avoid depending on the system locale
# TODO move this elsewhere
_WEEKDAYS_IT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
# TODO move this elsewhere
_MONTHS_IT = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)
# 16-point compass rose, one sector every 22.5 degrees
# TODO move this elsewhere
_COMPASS_IT = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO",
)

_MS_TO_KMH = 3.6
_FEET_TO_METERS = 0.3048

# ISA constants for reducing the station pressure to sea level
_ISA_SEA_LEVEL_TEMP = 288.15  # K
_ISA_LAPSE_RATE = 0.0065  # K/m
_ISA_EXPONENT = 5.25588  # g * M / (R * L)


def _qnh_from_station_pressure(pressure: float, elevation: float) -> float:
    """
    Reduces the station pressure (i.e., what the barometer reads, also known as QFE)
    to sea level according to the International Standard Atmosphere, which is what
    altimeters are calibrated against. Elevation is in meters.
    """
    return pressure * (1 - (_ISA_LAPSE_RATE * elevation) / _ISA_SEA_LEVEL_TEMP) ** -_ISA_EXPONENT


def _format_qnh(pressure: float | None, elevation: float) -> str | None:
    """QNH is always rounded down to the whole hPa, as per aviation convention."""
    if pressure is None:
        return None
    return str(math.floor(_qnh_from_station_pressure(pressure, elevation)))


def _format_date_it(dt: datetime.datetime) -> str:
    """Formats a date the Italian way, e.g. "dom 20 settembre 2026"."""
    return f"{_WEEKDAYS_IT[dt.weekday()]} {dt.day} {_MONTHS_IT[dt.month - 1]} {dt.year}"


def _format_time(dt: datetime.datetime) -> str:
    return dt.strftime("%H:%M")


def _format_number(value: float | None, decimals: int = 0) -> str | None:
    """Rounds a value for display, None (i.e., unavailable) passes through."""
    if value is None:
        return None
    if decimals <= 0:
        return str(round(value))
    return f"{value:.{decimals}f}"


def _compass_point(degrees: int) -> str:
    return _COMPASS_IT[round((degrees % 360) / 22.5) % 16]


class StaticDashboardGenerator:
    """Warning: this class does blocking I/O!!"""

    def __init__(self, config: dict):
        self.image_path = os.path.abspath(config["image_path"])
        # TODO one day we'll have templates
        # self.template_name = ...
        self.site = str(config.get("site", ""))
        self.runway_rotation = int(config.get("runway_rotation", 0)) % 360
        # airfield elevation, configured in feet but used in meters
        self.elevation = float(config.get("elevation_ft", 0)) * _FEET_TO_METERS
        self.width = int(config.get("width", 800))
        self.height = int(config.get("height", 600))
        self.rotate = int(config.get("rotate", 0)) % 360
        self.gray_levels = int(config.get("gray_levels", 16))
        self.dither = bool(config.get("dither", False))
        self.pdftoppm_cmd = str(config.get("pdftoppm_cmd", "pdftoppm"))
        self.template = load_template(self)

        if self.rotate not in (0, 90, 180, 270):
            raise ValueError(f"config: rotate must be one of: 0/90/180/270")
        if not 2 <= self.gray_levels <= 256:
            raise ValueError("config: gray_levels must be a number between 2 and 256")

        self._rasterizer = EInkImageRasterizer(self.gray_levels, self.pdftoppm_cmd)

    def _write_atomic(self, data: bytes) -> None:
        directory = os.path.dirname(self.image_path)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".png")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.image_path)
        except:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def _build_wind(self, data: SensorData) -> Wind:
        # 1.1 m/s roughly equivalent to 4 km/h
        # TODO use constant or convert from km/h
        if data.wind_direction is None or data.wind_speed is None or data.wind_speed < 1.1:
            dir_text = None
            # no direction, no arrow
            rotation = None
        else:
            direction = data.wind_direction % 360
            dir_text = f"da {direction}° · {_compass_point(direction)}"
            # the arrow points where the wind blows to, i.e., the opposite of where it comes from
            rotation = (direction + 180) % 360

        return Wind(
            speed=_format_number(data.wind_speed * _MS_TO_KMH if data.wind_speed is not None else None),
            gust=_format_number(data.gust_speed * _MS_TO_KMH if data.gust_speed is not None else None),
            dir_text=dir_text,
            rotation=rotation,
        )

    def _build_board(self, data: SensorData) -> Board:
        observed = data.timestamp.astimezone()
        updated = datetime.datetime.now()

        return Board(
            site=self.site,
            date=_format_date_it(updated),
            clock=_format_time(updated),
            runway=Runway(rotation=self.runway_rotation),
            # TODO compute the ephemerides
            ephem=Ephem("07:01", "19:24", "19:52", "5h 17m"),
            stamp=Stamp(observed=_format_time(observed), updated=_format_time(updated)),
            # TODO compute the verdict and the sky conditions
            verdict="Buone condizioni",
            sky="Parzialmente nuvoloso",
            temp=_format_number(data.temperature, 1),
            qnh=_format_qnh(data.pressure, self.elevation),
            dew=_format_number(data.dew_point, 1),
            rh=_format_number(data.humidity),
            # TODO cloud coverage and visibility are not sensor data
            clouds=None,
            vis=None,
            wind=self._build_wind(data),
        )

    def generate_dashboard(self, data: SensorData):
        board = self._build_board(data)
        html = self.template.render(**asdict(board))
        img = self._rasterizer.process(html, self.width, self.height, self.rotate)
        self._write_atomic(img)
        _LOGGER.info(f"static dashboard saved to {self.image_path}")


class EInkImageRasterizer:
    """Warning: this class does blocking I/O!!"""

    def __init__(self, gray_levels: int, pdftoppm_cmd: str):
        self.gray_levels = gray_levels
        self.pdftoppm_cmd = pdftoppm_cmd

        # some image manipulation dark magic
        step = 255 / (self.gray_levels - 1)
        self._levels = [round(i * step) for i in range(self.gray_levels)]
        # for "antialiasing"
        self._lut = [round(round(v / step) * step) for v in range(256)]

    def process(self, html: str, width: int, height: int, rotate: int) -> bytes:
        # turn HTML into PDF
        pdf = HTML(string=html).write_pdf()
        # turn PDF into PNG and post-process the image
        png = self._rasterize(pdf, width, height)
        return self._postprocess(png, rotate)

    def _rasterize(self, pdf: bytes, width: int, height: int) -> bytes:
        with tempfile.TemporaryDirectory(prefix="metar-render-") as tmp:
            pdf_path = os.path.join(tmp, "page.pdf")
            out_root = os.path.join(tmp, "page")
            with open(pdf_path, "wb") as f:
                f.write(pdf)

            cmd = [
                self.pdftoppm_cmd,
                "-png", "-gray", "-singlefile",
                "-f", "1", "-l", "1",
                "-scale-to-x", str(width),
                "-scale-to-y", str(height),
                pdf_path, out_root,
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                err = e.stderr.decode(errors="replace").strip()
                raise RuntimeError(f"call to pdftoppm failed (exit {e.returncode}): {err}") from e

            with open(out_root + ".png", "rb") as f:
                return f.read()

    def _postprocess(self, png: bytes, rotate: int) -> bytes:
        with Image.open(io.BytesIO(png)) as src:
            # L = 8-bit grayscale
            img = src.convert("L")

        img = img.point(self._lut)

        if rotate:
            img = img.transpose(_ROTATE_TRANSPOSE[rotate])

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
