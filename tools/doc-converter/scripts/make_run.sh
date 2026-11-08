#!/bin/bash
# Convert and verify into a run directory laid out for compare_ref.sh.
#
# usage: scripts/make_run.sh <pdf> <run dir> <profile>
#   <run dir>/out/                  converter output
#   <run dir>/verify/<file>.md.txt  verifier report per section file
#   <run dir>/convert-stdout.txt    converter log
# Relative paths are relative to the current directory. The run directory is deleted and recreated;
# a non-empty directory that is not an earlier run (no convert-stdout.txt) is refused.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 <pdf> <run dir> <profile>" >&2
  exit 2
fi

absolute() {
  case $1 in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$PWD/$1" ;;
  esac
}

PDF=$(absolute "$1")
RUN=$(absolute "$2")
PROFILE=$3
if [ -d "$PROFILE" ]; then
  PROFILE=$(absolute "$PROFILE")
fi

if [ ! -f "$PDF" ]; then
  echo "$0: no such PDF: $PDF" >&2
  exit 2
fi
if [ -n "$(ls -A "$RUN" 2>/dev/null)" ] && [ ! -e "$RUN/convert-stdout.txt" ]; then
  echo "$0: refusing to delete $RUN: it is not an earlier run directory (no convert-stdout.txt)" >&2
  exit 2
fi

cd "$(dirname "$0")/.."

rm -rf "$RUN"
mkdir -p "$RUN"
uv run --quiet doc-converter convert --input "$PDF" --output "$RUN/out" --profile "$PROFILE" > "$RUN/convert-stdout.txt"
uv run --quiet doc-converter verify --input "$PDF" --output "$RUN/out" --profile "$PROFILE" --report-dir "$RUN/verify"
echo "run written to $RUN"
