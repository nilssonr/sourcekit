#!/bin/bash
# Compare a conversion run with a saved reference run.
#
# usage: scripts/compare_ref.sh <ref dir> <run dir>
#
# Both directories have the same layout:
#   out/      converter output (Markdown, illustrations/*.png, generated JSON)
#   verify/   verifier reports
# Exits 0 only when every reference file exists in the run and is identical: byte for byte,
# except out/hyphen-decisions.json, which is compared as a multiset of entries (its order follows
# assembly order; design decision D1). Files only in the run are listed but are not a failure.
# Holds no book content.
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: $0 <ref dir> <run dir>" >&2
  exit 2
fi

exec python3 - "$1" "$2" <<'PY'
import filecmp
import json
import sys
from collections import Counter
from pathlib import Path

ref, run = Path(sys.argv[1]), Path(sys.argv[2])
MULTISET = {"out/hyphen-decisions.json"}


def entries(path: Path) -> Counter:
    return Counter(json.dumps(d, sort_keys=True) for d in json.loads(path.read_text(encoding="utf-8")))


fail = False
for sub in ("out", "verify"):
    if not (ref / sub).is_dir() or not (run / sub).is_dir():
        print(f"missing {sub}/ in {ref} or {run}", file=sys.stderr)
        sys.exit(2)
    rfiles = {p.relative_to(ref).as_posix() for p in (ref / sub).rglob("*") if p.is_file()}
    nfiles = {p.relative_to(run).as_posix() for p in (run / sub).rglob("*") if p.is_file()}
    bad = []
    for f in sorted(rfiles):
        if f not in nfiles:
            bad.append(f"missing: {f}")
        elif f in MULTISET:
            if entries(ref / f) != entries(run / f):
                bad.append(f"entries differ: {f}")
        elif not filecmp.cmp(ref / f, run / f, shallow=False):
            bad.append(f"differs: {f}")
    for line in bad:
        print(line, file=sys.stderr)
    new = sorted(nfiles - rfiles)
    status = "identical" if not bad else f"{len(bad)} DIFFERENT"
    print(f"{sub}/: {status} ({len(rfiles)} reference files)" + (f"; new: {' '.join(new)}" if new else ""))
    fail = fail or bool(bad)

if fail:
    print(f"DIFFERENT (inspect with: diff -r {ref}/out {run}/out)", file=sys.stderr)
    sys.exit(1)
print("IDENTICAL")
PY
