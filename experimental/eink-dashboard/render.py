#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["jinja2", "pypdfium2", "weasyprint"]
# ///

from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pypdfium2 as pdfium
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from weasyprint import HTML

HERE = Path(__file__).parent
DASH = "--"


@dataclass
class Wind:
    speed: str | None = None       # "14"
    gust: str | None = None        # "26"
    dir_text: str | None = None    # "da 270° · O"
    rotation: int | None = None    # degrees; None => arrow not displayed


@dataclass
class Runway:
    rotation: int = 140            # runway QFU, from configuration


@dataclass
class Ephem:
    sunrise: str = DASH
    sunset: str = DASH
    twilight: str = DASH
    daylight: str = DASH


@dataclass
class Stamp:
    observed: str = DASH + ':' + DASH
    updated: str = DASH + ':' + DASH


@dataclass
class Board:
    site: str = ""
    date: str = ""
    clock: str = ""
    runway: Runway = field(default_factory=Runway)
    ephem: Ephem = field(default_factory=Ephem)
    stamp: Stamp = field(default_factory=Stamp)

    # con default a "--" quando il dato manca
    verdict: str | None = None
    sky: str | None = None
    temp: str | None = None
    qnh: str | None = None
    dew: str | None = None
    rh: str | None = None
    clouds: str | None = None
    vis: str | None = None
    wind: Wind = field(default_factory=Wind)


def dash(value):
    return DASH if value is None or value == "" else value


def build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(HERE),
        undefined=StrictUndefined,
        autoescape=True,
    )
    env.filters["dash"] = dash
    return env


def render_html(board: Board) -> str:
    return build_env().get_template(sys.argv[1]).render(**asdict(board))


def render_png(board: Board, path: Path) -> None:
    doc = HTML(string=render_html(board), base_url=str(HERE)).render()
    if len(doc.pages) != 1:
        raise RuntimeError(
            f"something went wrong: {len(doc.pages)} pages instead of 1. Please check rendering."
        )
    page = pdfium.PdfDocument(io.BytesIO(doc.write_pdf()))[0]
    img = page.render(scale=96 / 72, grayscale=True).to_pil()
    if img.size != (800, 600):
        raise RuntimeError(f"unexpected size: {img.size}")
    img.save(path)


# --------------------------------------------------------------------------

def demo_empty() -> Board:
    return Board(
        site="Aviosuperficie Valle del Ticino",
        date="dom 20 settembre 2026",
        clock="14:35",
        ephem=Ephem("07:01", "19:24", "19:52", "5h 17m"),
        stamp=Stamp(observed=DASH, updated="14:35"),
    )


def demo_full() -> Board:
    return Board(
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


if __name__ == "__main__":
    render_png(demo_empty(), HERE / "preview-empty.png")
    render_png(demo_full(), HERE / "preview-full.png")
