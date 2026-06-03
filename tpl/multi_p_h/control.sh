#!/usr/bin/env bash
set -euo pipefail

readonly NAME="multi_p_h"
readonly BASE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

EXECUTE_DIR="$BASE_DIR"
while [[ $# -gt 0 ]]; do
  case $1 in
    -d) shift; EXECUTE_DIR=$1; shift ;;
    *) break ;;
  esac
done

[[ -d $EXECUTE_DIR ]] || { echo "ERROR: $EXECUTE_DIR is not a directory" >&2; exit 1; }
mkdir -p "$EXECUTE_DIR/logs"
readonly PID_FILE="$EXECUTE_DIR/logs/$NAME.pid"

is_running() {
  [[ -f $PID_FILE ]] || return 1
  local pid
  pid=$(<"$PID_FILE")
  [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null
}

start() {
  if is_running; then
    echo "$NAME already running (pid $(<"$PID_FILE"))"
    return 0
  fi
  echo "starting $NAME..."
  (
    cd "$EXECUTE_DIR"
    nohup python3 -m "$NAME.main" -d "$EXECUTE_DIR" \
      >"$EXECUTE_DIR/logs/$NAME.out" \
      2>"$EXECUTE_DIR/logs/$NAME.err" &
    echo $! > "$PID_FILE"
  )
  sleep 1
  status
}

stop() {
  if ! is_running; then
    echo "$NAME not running"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid=$(<"$PID_FILE")
  echo "stopping $NAME (pid $pid)..."
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for ((i=0; i<30; i++)); do
    is_running || break
    sleep 1
  done
  if is_running; then
    echo "force killing $NAME"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "stopped"
}

status() {
  if is_running; then
    echo "$NAME running (pid $(<"$PID_FILE"))"
  else
    echo "$NAME not running"
  fi
}

case ${1:-} in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  *) echo "usage: $0 [-d EXECUTE_DIR] {start|stop|restart|status}" >&2; exit 1 ;;
esac
