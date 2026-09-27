#!/data/data/com.termux/files/usr/bin/bash
# Stop MDev Panel. Any running instances are stopped gracefully first
# (world saved), which can take up to the STOP_TIMEOUT in config.py.

cd "$(dirname "$0")" || exit 1

PIDFILE="panel.pid"

if [ ! -f "$PIDFILE" ]; then
  echo "No panel.pid found. The panel is not running, or it was not started with start_panel.sh."
  exit 1
fi

PID="$(cat "$PIDFILE")"

if ! kill -0 "$PID" 2>/dev/null; then
  echo "MDev Panel is not running (stale PID file removed)."
  rm -f "$PIDFILE"
  exit 0
fi

echo "Stopping MDev Panel (PID $PID). Any running instances are stopped first..."
kill -TERM "$PID"

for _ in $(seq 1 150); do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "MDev Panel stopped."
    if command -v termux-wake-unlock >/dev/null 2>&1; then
      termux-wake-unlock
    fi
    exit 0
  fi
  sleep 1
done

echo "The panel is still shutting down after 150 seconds."
echo "Check panel.log, or run: kill -9 $PID  (this may skip saving any running instance's world)"
exit 1
