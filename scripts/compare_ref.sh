#!/bin/bash
# Compare a conversion run with a saved reference run.
#
# usage: scripts/compare_ref.sh <ref dir> <run dir>
#
# Both directories have the same layout:
#   out/      converter output (Markdown, illustrations/*.png, generated JSON)
#   verify/   verifier report per Markdown file (<file>.md.txt)
# Exits 0 only when both trees are byte-identical. Holds no book content.
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <ref dir> <run dir>" >&2
  exit 2
fi
REF=$1
RUN=$2
fail=0

for sub in out verify; do
  if [ ! -d "$REF/$sub" ] || [ ! -d "$RUN/$sub" ]; then
    echo "missing $sub/ in $REF or $RUN" >&2
    exit 2
  fi
  if diff -rq "$REF/$sub" "$RUN/$sub"; then
    n=$(find "$REF/$sub" -type f | wc -l | tr -d ' ')
    echo "$sub/: identical ($n files)"
  else
    fail=1
  fi
done

if [ $fail -ne 0 ]; then
  echo "DIFFERENT (inspect with: diff -r $REF/out $RUN/out)" >&2
  exit 1
fi
echo "IDENTICAL"
