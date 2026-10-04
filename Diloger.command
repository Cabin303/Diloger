#!/bin/bash
# Diloger launcher.
#
# Works from Terminal and from a Finder double-click. Never uses offscreen
# rendering, and never hides diagnostics: any failure is printed, written to a
# log file, and the Terminal window is held open until the user acknowledges it.

set -u

# Resolve the project directory from this script's own location so the launcher
# does not depend on the caller's current directory.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
PROJECT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

cd "$PROJECT_DIR" || {
  echo "Diloger: cannot enter project folder: $PROJECT_DIR"
  read -r -p "Press Enter to close this window." _
  exit 1
}

# Use only the project virtual environment, independent of any activated shell.
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/launch.log"

# A stale PYTHONPATH/PYTHONHOME from the caller's environment would break the
# bundled venv, so drop it instead of trusting it.
unset PYTHONPATH PYTHONHOME PYTHONSTARTUP
# Prepend the venv but keep the rest of the caller's PATH. Replacing it outright
# hid /opt/homebrew/bin, so the app could not discover the user's local
# whisper-cli and every transcription aborted before it started.
export PATH="$PROJECT_DIR/.venv/bin:$PATH"
export QT_LOGGING_RULES="${QT_LOGGING_RULES:-qt.multimedia.ffmpeg=false}"

die() {
  echo ""
  echo "Diloger could not start."
  echo "  $1"
  echo ""
  if [ -f "$LOG_FILE" ]; then
    echo "Last output:"
    tail -n 40 "$LOG_FILE"
    echo ""
  fi
  echo "Full log: $LOG_FILE"
  read -r -p "Press Enter to close this window." _
  exit 1
}

if [ ! -x "$VENV_PYTHON" ]; then
  die "Missing project environment: $VENV_PYTHON
Recreate it with:
  python3 -m venv .venv
  .venv/bin/pip install -e ."
fi

mkdir -p "$LOG_DIR" 2>/dev/null

VERSION="$("$VENV_PYTHON" --version 2>&1)" || die "Cannot run project Python."
echo "Diloger starting ($VERSION)"
echo "Project: $PROJECT_DIR"
echo "Log: $LOG_FILE"
echo ""

# Append rather than truncate so repeated launches keep their history.
{
  echo "===== launch $(date '+%Y-%m-%d %H:%M:%S') ====="
} >>"$LOG_FILE" 2>/dev/null

"$VENV_PYTHON" -m diloger.app.main >>"$LOG_FILE" 2>&1
STATUS=$?

if [ $STATUS -ne 0 ]; then
  die "Application exited with code $STATUS"
fi

# Clean exit (user closed the window) needs no pause.
exit 0