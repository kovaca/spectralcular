#!/usr/bin/env bash
# Rebuild "Spectralcular Scenery" from its selection manifest:
#   init → import → lock → build → print
#
#   examples/spectralcular-scenery/build.sh <project-dir>
#
# <project-dir> needs ~25 GB free (bands, index rasters, plate images, PDFs).
# The build downloads Sentinel-2 bands from Earth Search; set
# SPECTRALCULAR_CONTACT (an email or URL) for the OpenStreetMap tile requests.
# Re-running resumes: finished spreads are skipped.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <project-dir>" >&2
  exit 2
fi
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
dir="$1"
sc() { uv run --project "$repo" spectralcular --project-dir "$dir" "$@"; }

if [ ! -f "$dir/config/book_specs.yaml" ]; then
  uv run --project "$repo" spectralcular init "$dir" --book "$here/book.yaml"
fi
sc import "$here/selection.json"
sc candidates lock --force
sc -v build --skip-existing --keep-going
sc -v print
echo "print files: $dir/output/print/"
