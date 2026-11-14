#!/usr/bin/env bash
# Render docs/assets/charts/*.svg to PNG for the README (GitHub shows PNGs the same everywhere).
# The image fmg-bench-svg renders with resvg and the Windows fonts mounted read-only, so the CJK glyphs
# do not depend on the viewer. No network is given to the container.
#
#     python bench/charts.py && bash bench/render_charts.sh            # every chart
#     bash bench/render_charts.sh ranking.zh.light forgetting.zh.dark  # only these (names without extension)
set -euo pipefail

FONTS="${FONTS_DIR:-C:/Windows/Fonts}"
WIDTH="${WIDTH:-1600}"

cd "$(dirname "$0")/.."
# the mount path must be one docker understands: on Git Bash on Windows that is the drive form from pwd -W
DIR="${CHARTS_DIR:-$(pwd -W 2>/dev/null || pwd)/docs/assets/charts}"
if [ "$#" -gt 0 ]; then names=("$@"); else names=(); for svg in docs/assets/charts/*.svg; do names+=("$(basename "$svg" .svg)"); done; fi
for name in "${names[@]}"; do
  MSYS_NO_PATHCONV=1 docker run --rm --network none \
    -v "$DIR:/in:ro" -v "$DIR:/out" -v "$FONTS:/fonts:ro" \
    fmg-bench-svg "/in/$name.svg" "/out/$name.png" "$WIDTH"
done
echo "rendered $(ls docs/assets/charts/*.png | wc -l) PNG file(s)"
