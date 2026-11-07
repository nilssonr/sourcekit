#!/bin/bash
# Compare a conversion run with a saved reference run.
#
# usage: scripts/compare_ref.sh <ref dir> <run dir>
#
# Both directories have the same layout:
#   out/      converter output (Markdown, illustrations/*.png, generated JSON)
#   verify/   verifier report per Markdown file (<file>.md.txt)
# Exits 0 only when both trees are identical: every file byte for byte, except
# out/hyphen-decisions.json, which is compared as a multiset of entries (its order follows
# assembly order; design decision D1). Holds no book content.
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
  if diff -rq -x hyphen-decisions.json "$REF/$sub" "$RUN/$sub"; then
    n=$(find "$REF/$sub" -type f | wc -l | tr -d ' ')
    echo "$sub/: identical ($n files)"
  else
    fail=1
  fi
done

HD=out/hyphen-decisions.json
if [ -f "$REF/$HD" ] || [ -f "$RUN/$HD" ]; then
  if python3 - "$REF/$HD" "$RUN/$HD" <<'PY'
import json, sys
from collections import Counter
def entries(path):
    return Counter(json.dumps(d, sort_keys=True) for d in json.load(open(path, encoding="utf-8")))
sys.exit(0 if entries(sys.argv[1]) == entries(sys.argv[2]) else 1)
PY
  then
    echo "$HD: same entries"
  else
    echo "$HD: entries differ" >&2
    fail=1
  fi
fi

if [ $fail -ne 0 ]; then
  echo "DIFFERENT (inspect with: diff -r $REF/out $RUN/out)" >&2
  exit 1
fi
echo "IDENTICAL"
