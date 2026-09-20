#!/usr/bin/env bash

set -euo pipefail

chromium --headless --disable-gpu --hide-scrollbars \
           --screenshot=board.rendered.png --window-size=800,600 \
           --force-device-scale-factor=1 "$1"

convert board.rendered.png -colorspace Gray -dither FloydSteinberg -rotate 90 -colors 16 -depth 8 board.output.png
