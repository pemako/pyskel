#!/usr/bin/env bash
# End-to-end smoke test for all 7 templates.
# Each template: generate → pip install → control.sh start → exercise → stop → check log.
# Runs in /tmp by default; pass an alternate WORK= env to use a different dir.

set -uo pipefail

# Resolve the repo root from this script's location so the test runs from
# anywhere (CI, local checkout, anywhere).
readonly REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PYSKEL="$REPO/pyskel"
readonly WORK="${WORK:-/tmp/pyskel-e2e-$$}"

[[ -x $PYSKEL ]] || { echo "ERROR: $PYSKEL not executable" >&2; exit 1; }

rm -rf "$WORK"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

R=$'\e[1;31m'; G=$'\e[1;32m'; Y=$'\e[1;33m'; D=$'\e[2m'; N=$'\e[0m'

declare -i PASSED=0
declare -i FAILED=0
declare -a FAIL_NAMES=()

check() {
  local name=$1 status=$2 evidence=$3
  if [[ $status == OK ]]; then
    printf '%s✓%s %-12s %s%s%s\n' "$G" "$N" "$name" "$D" "$evidence" "$N"
    PASSED+=1
  else
    printf '%s✗%s %-12s %s\n' "$R" "$N" "$name" "$evidence"
    FAILED+=1
    FAIL_NAMES+=("$name")
  fi
}

# Generate + install + start helper. Sets $LOG_PATH for the caller.
prepare() {
  local tpl=$1 name=$2
  cd "$WORK"
  "$PYSKEL" "$tpl" "$name" >/dev/null
  cd "$name"
  python3 -m venv .venv >/dev/null 2>&1
  .venv/bin/pip install -q -e . >/dev/null 2>&1
}

run_simple() {
  local n=simple gen=s_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 2
  local lines=$(grep -c "running" "logs/$gen.log" 2>/dev/null || echo 0)
  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5
  if ! grep -qE "stop|stopping" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no stop log"; return
  fi
  check "$n" OK "loop ran ($lines tick logs), clean stop"
}

run_multi_t() {
  local n=multi_t gen=mt_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 2
  local workers=$(grep -oE "worker-[0-9]+" "logs/$gen.log" 2>/dev/null | sort -u | wc -l | tr -d ' ')
  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5
  if ! grep -qE "service stop" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no clean stop log"; return
  fi
  check "$n" OK "$workers workers, clean stop"
}

run_multi_t_q() {
  local n=multi_t_q gen=mtq_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 3
  local processed=$(grep -c "processing Task" "logs/$gen.log" 2>/dev/null || echo 0)
  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5
  if ! grep -q "service stopped" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no clean stop log"; return
  fi
  check "$n" OK "$processed tasks processed, clean stop"
}

run_multi_p() {
  local n=multi_p gen=mp_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 3
  local pids=$(grep -oE "pid=[0-9]+" "logs/$gen.log" 2>/dev/null | sort -u | wc -l | tr -d ' ')
  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5
  if ! grep -q "service stopped" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no clean stop log"; return
  fi
  check "$n" OK "$pids pids in single log, clean stop"
}

run_multi_p_h() {
  local n=multi_p_h gen=mph_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 4

  local ping=$(curl -sS http://127.0.0.1:8000/ping 2>/dev/null || echo "")
  local echo=$(curl -sS -X POST http://127.0.0.1:8000/echo \
    -H 'content-type: application/json' \
    -d '{"message":"hi"}' 2>/dev/null || echo "")
  local docs=$(curl -sS -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/docs 2>/dev/null || echo 0)

  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5

  [[ $ping == *"pong"* ]] || { check "$n" FAIL "ping=$ping"; return; }
  [[ $echo == *"hi"* ]] || { check "$n" FAIL "echo=$echo"; return; }
  [[ $docs == "200" ]] || { check "$n" FAIL "docs=$docs"; return; }

  check "$n" OK "ping/echo/docs ok, clean stop"
}

run_multi_p_g() {
  local n=multi_p_g gen=mpg_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 3

  local result
  result=$(.venv/bin/python - <<EOF 2>&1
import grpc
from $gen.pb import service_pb2, service_pb2_grpc
with grpc.insecure_channel('127.0.0.1:50051') as ch:
    stub = service_pb2_grpc.PingServiceStub(ch)
    print('ping=' + stub.Ping(service_pb2.PingRequest()).message)
    print('echo=' + stub.Echo(service_pb2.EchoRequest(message='hello')).text)
EOF
  )

  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5

  [[ $result == *"ping=pong"* && $result == *"echo=hello"* ]] || {
    check "$n" FAIL "client: $result"; return
  }
  if ! grep -q "service stopped" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no clean stop log"; return
  fi
  check "$n" OK "ping=pong, echo round-trip, clean stop"
}

run_multi_p_t() {
  local n=multi_p_t gen=mpt_test
  prepare "$n" "$gen"
  PATH=".venv/bin:$PATH" ./control.sh start >/dev/null
  sleep 3

  local result
  result=$(.venv/bin/python - <<EOF 2>&1
from thrift.transport import TSocket, TTransport
from thrift.protocol import TBinaryProtocol
from $gen.pb.tsvc import PingService
t = TSocket.TSocket('127.0.0.1', 9090)
t = TTransport.TBufferedTransport(t)
prot = TBinaryProtocol.TBinaryProtocol(t)
client = PingService.Client(prot)
t.open()
print('ping=' + client.Ping())
print('echo=' + client.Echo('hello'))
t.close()
EOF
  )

  PATH=".venv/bin:$PATH" ./control.sh stop >/dev/null
  sleep 0.5

  [[ $result == *"ping=pong"* && $result == *"echo=hello"* ]] || {
    check "$n" FAIL "client: $result"; return
  }
  if ! grep -q "service stopped" "logs/$gen.log" 2>/dev/null; then
    check "$n" FAIL "no clean stop log"; return
  fi
  check "$n" OK "ping=pong, echo round-trip, clean stop"
}

echo "${Y}pyskel e2e suite${N}  (work dir: ${WORK})"
echo

run_simple
run_multi_t
run_multi_t_q
run_multi_p
run_multi_p_h
run_multi_p_g
run_multi_p_t

echo
echo "─────────────────────────────────"
if (( FAILED == 0 )); then
  echo "${G}all $PASSED templates passed${N}"
else
  echo "${R}$FAILED failed${N}: ${FAIL_NAMES[*]}    ${G}$PASSED passed${N}"
fi

exit "$FAILED"
