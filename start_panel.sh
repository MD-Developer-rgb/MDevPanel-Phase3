#!/data/data/com.termux/files/usr/bin/bash
# Start MDev Panel (instance manager).
#   ./start_panel.sh                run in this terminal (Ctrl+C stops the panel and any running instances)
#   ./start_panel.sh --background   run in the background (stop with ./stop_panel.sh)

cd "$(dirname "$0")" || exit 1

PIDFILE="panel.pid"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "MDev Panel is already running (PID $(cat "$PIDFILE")). Stop it with ./stop_panel.sh"
  exit 1
fi

# Keep Android from putting Termux (and Minecraft) to sleep. (Optional, only if the command exists.)
if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock
fi

PYTHON="python3"
command -v python3 >/dev/null 2>&1 || PYTHON="python"

if [ "$1" = "--background" ] || [ "$1" = "-b" ]; then
  nohup "$PYTHON" app.py > panel.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1
  if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "MDev Panel started in the background (PID $(cat "$PIDFILE"))."
    echo "Startup details: cat panel.log"
  else
    rm -f "$PIDFILE"
    echo "MDev Panel failed to start. See panel.log:"
    tail -n 20 panel.log
    exit 1
  fi
else
  echo $$ > "$PIDFILE"
  exec "$PYTHON" app.py   # exec keeps the same PID, so stop_panel.sh signals the panel itself
fi
