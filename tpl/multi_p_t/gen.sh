#!/usr/bin/env bash
# Regenerate Apache Thrift Python stubs from proto/*.thrift into multi_p_t/pb/.
# Run from project root: ./gen.sh
# Requires: thrift CLI on PATH (brew install thrift / apt install thrift-compiler)
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

readonly PKG="multi_p_t"
readonly PROTO_DIR="proto"
readonly OUT_DIR="$PKG/pb"

if ! command -v thrift >/dev/null 2>&1; then
  echo "ERROR: thrift CLI not on PATH." >&2
  echo "  macOS:  brew install thrift" >&2
  echo "  Debian: apt install thrift-compiler" >&2
  exit 1
fi

shopt -s nullglob
thrift_files=("$PROTO_DIR"/*.thrift)
shopt -u nullglob

if (( ${#thrift_files[@]} == 0 )); then
  echo "ERROR: no .thrift files in $PROTO_DIR/" >&2
  exit 1
fi

# Wipe previous output (excluding our own __init__.py at the top of pb/).
find "$OUT_DIR" -mindepth 1 -maxdepth 1 ! -name '__init__.py' -exec rm -rf {} +

for f in "${thrift_files[@]}"; do
  thrift --gen py:slots -out "$OUT_DIR" "$f"
done

# Apache Thrift's Python generator emits `*-remote` CLI client scripts
# that aren't Python modules and aren't useful for this template — drop them.
find "$OUT_DIR" -name '*-remote' -type f -delete

# Apache Thrift's Python generator emits absolute imports keyed off the
# `namespace py X` value, e.g. `from service.ttypes import *`. Since we
# place the generated tree inside multi_p_t/pb/, those absolute paths
# don't resolve. Patch them to relative imports within the namespace
# subpackage so they import as multi_p_t.pb.service.<x>.
python3 - "$OUT_DIR" <<'PY'
import pathlib, re, sys
out = pathlib.Path(sys.argv[1])
# For each <namespace>/ directory under pb/, patch its files.
for ns_dir in out.iterdir():
    if not ns_dir.is_dir() or ns_dir.name.startswith("_"):
        continue
    ns = ns_dir.name
    for py in ns_dir.rglob("*.py"):
        text = py.read_text()
        # `from <ns>.foo import x` -> `from .foo import x`
        # `from <ns>.foo.bar import x` -> `from .foo.bar import x`
        patched = re.sub(
            rf"^from {re.escape(ns)}\.",
            "from .",
            text,
            flags=re.M,
        )
        # bare `import <ns>.foo` -> `from . import foo`
        patched = re.sub(
            rf"^import {re.escape(ns)}\.(\w+)\b",
            r"from . import \1",
            patched,
            flags=re.M,
        )
        if patched != text:
            py.write_text(patched)
PY

echo "regenerated stubs in $OUT_DIR/"
find "$OUT_DIR" -name '*.py' -not -name '__init__.py' | sort
