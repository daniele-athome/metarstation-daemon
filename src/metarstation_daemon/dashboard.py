import io
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict

from PIL import Image
from jinja2 import Environment, PackageLoader, StrictUndefined
from weasyprint import HTML

from .data import SensorData

_LOGGER = logging.getLogger(__name__)

# maps clockwise rotation angle to PIL rotation constants
_ROTATE_TRANSPOSE = {
    90: Image.ROTATE_270,
    180: Image.ROTATE_180,
    270: Image.ROTATE_90,
}

STRING_DASH = "--"


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


class StaticDashboardGenerator:
    """Warning: this class does blocking I/O!!"""

    def __init__(self, config: dict):
        self.image_path = os.path.abspath(config["image_path"])
        # TODO one day we'll have templates
        # self.template_name = ...
        self.width = int(config.get("width", 800))
        self.height = int(config.get("height", 600))
        self.rotate = int(config.get("rotate", 90)) % 360
        self.gray_levels = int(config.get("gray_levels", 16))
        self.dither = bool(config.get("dither", False))
        self.pdftoppm_cmd = str(config.get("pdftoppm_cmd", "pdftoppm"))

        if self.rotate not in (0, 90, 180, 270):
            raise ValueError(f"config: rotate must be one of: 0/90/180/270")
        if not 2 <= self.gray_levels <= 256:
            raise ValueError("config: gray_levels must be a number between 2 and 256")

        self._rasterizer = EInkImageRasterizer(self.gray_levels, self.pdftoppm_cmd)

    def _load_template(self):
        # hard-coded template file for now :P
        return _build_jinja_env().get_template("dashboard_it.800x600.html.j2")

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

    def generate_dashboard(self, data: SensorData):
        # TODO translate SensorData into a Board object
        board = Board(
            site="Aviosuperficie Valle del Ticino",
            date="dom 20 settembre 2026",
            clock="14:35",
            ephem=Ephem("07:01", "19:24", "19:52", "5h 17m"),
            stamp=Stamp(observed="14:30", updated="14:35"),
            verdict="Buone condizioni",
            sky="Parzialmente nuvoloso",
            temp="18.4", qnh="1016", dew="11.2", rh="63", clouds="40", vis="18",
            wind=Wind(speed="14", gust="26", dir_text="da 270° · O", rotation=90),
        )
        # board = Board(
        #     site="Aviosuperficie Valle del Ticino",
        #     date="dom 20 settembre 2026",
        #     clock="14:35",
        # )
        html = self._load_template().render(**asdict(board))
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
