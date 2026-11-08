#!/bin/bash
# Convert and verify into a run directory laid out for compare_ref.sh.
#
# usage: scripts/make_run.sh <pdf> <run dir> <profile>
#   <run dir>/out/                  converter output
#   <run dir>/verify/<file>.md.txt  verifier report per section file
#   <run dir>/convert-stdout.txt    converter log
# The run directory is deleted and recreated.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 <pdf> <run dir> <profile>" >&2
  exit 2
fi
PDF=$1
RUN=$2
PROFILE=$3
cd "$(dirname "$0")/.."

rm -rf "$RUN"
mkdir -p "$RUN"
uv run --quiet doc-converter convert --input "$PDF" --output "$RUN/out" --profile "$PROFILE" > "$RUN/convert-stdout.txt"
uv run --quiet doc-converter verify --input "$PDF" --output "$RUN/out" --profile "$PROFILE" --report-dir "$RUN/verify"
echo "run written to $RUN"
