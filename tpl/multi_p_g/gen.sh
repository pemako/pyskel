#!/usr/bin/env bash
# Regenerate gRPC Python stubs from proto/*.proto into multi_p_g/pb/.
# Run from project root: ./gen.sh
# Requires: pip install -e '.[dev]'   (gives grpcio-tools)
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

readonly PKG="multi_p_g"
readonly PROTO_DIR="proto"
readonly OUT_DIR="$PKG/pb"

if ! python3 -c 'import grpc_tools' 2>/dev/null; then
  echo "ERROR: grpcio-tools not installed. Run: pip install -e '.[dev]'" >&2
  exit 1
fi

# Generate every .proto in proto/ into the package's pb/ directory.
shopt -s nullglob
proto_files=("$PROTO_DIR"/*.proto)
shopt -u nullglob

if (( ${#proto_files[@]} == 0 )); then
  echo "ERROR: no .proto files in $PROTO_DIR/" >&2
  exit 1
fi

python3 -m grpc_tools.protoc \
  -I="$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  "${proto_files[@]}"

# protoc generates `import service_pb2` (top-level), but both files now
# live inside the pb/ subpackage. Patch the imports to be relative.
python3 - "$OUT_DIR" <<'PY'
import pathlib, re, sys
out_dir = pathlib.Path(sys.argv[1])
for f in out_dir.glob("*_pb2_grpc.py"):
    text = f.read_text()
    patched = re.sub(
        r"^import (\w+_pb2)\b",
        r"from . import \1",
        text,
        flags=re.M,
    )
    if patched != text:
        f.write_text(patched)
PY

echo "regenerated stubs in $OUT_DIR/"
ls "$OUT_DIR"/*.py
