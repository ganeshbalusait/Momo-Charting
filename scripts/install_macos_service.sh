#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
SOURCE_PLIST="$PROJECT_DIR/deployment/com.agenticai.trading.plist"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="$LAUNCH_AGENTS_DIR/com.agenticai.trading.plist"
RUNTIME_ROOT="${HOME}/Library/Application Support/AgenticAI-Trading"
RUNTIME_APP="$RUNTIME_ROOT/app"
SERVICE_DOMAIN="gui/$(id -u)"
SERVICE_NAME="$SERVICE_DOMAIN/com.agenticai.trading"

mkdir -p "$LAUNCH_AGENTS_DIR" "$PROJECT_DIR/artifacts/logs" "$RUNTIME_APP"

if [[ ! -x "$PROJECT_DIR/scripts/run_macos_production.sh" ]]; then
  print -u2 "Production runner is missing or not executable: $PROJECT_DIR/scripts/run_macos_production.sh"
  exit 78
fi

if [[ ! -f "$PROJECT_DIR/frontend/dist/index.html" ]]; then
  print -u2 "Production frontend is missing. Run: cd frontend && pnpm run build"
  exit 78
fi

if [[ ! -x "$PROJECT_DIR/.venv-macos/bin/python" && ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  print -u2 "Python virtual environment is missing. Expected .venv-macos or .venv."
  exit 78
fi

plutil -lint "$SOURCE_PLIST" >/dev/null

# Background LaunchAgents cannot reliably read projects stored in macOS's
# protected Documents folder. Publish a private runtime copy under Library,
# while preserving its production database and encrypted credentials across
# later code updates.
/usr/bin/rsync -a \
  --exclude '.git/' \
  --exclude '.pnpm-store/' \
  --exclude '.venv/' \
  --exclude '.venv-macos/' \
  --exclude 'Momo-Chart/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/.pnpm-store/' \
  --exclude 'database/*.db' \
  --exclude 'artifacts/' \
  --exclude 'tests/' \
  "$PROJECT_DIR/" "$RUNTIME_APP/"

/usr/bin/rsync -a "$PROJECT_DIR/.venv-macos/" "$RUNTIME_APP/.venv-macos/"

mkdir -p "$RUNTIME_APP/database" "$RUNTIME_APP/artifacts/logs"
if [[ ! -f "$RUNTIME_APP/database/trades.db" ]]; then
  /usr/bin/sqlite3 "$PROJECT_DIR/database/trades.db" ".backup '$RUNTIME_APP/database/trades.db'"
fi

/usr/bin/rsync -a --ignore-existing \
  --exclude '*.db' \
  --exclude '*.log' \
  --exclude 'coding_loop/' \
  --exclude 'learning/' \
  --exclude 'logs/' \
  --exclude 'loop_engineering/' \
  "$PROJECT_DIR/artifacts/" "$RUNTIME_APP/artifacts/"

chmod 700 "$RUNTIME_ROOT" "$RUNTIME_APP"
[[ -f "$RUNTIME_APP/.env" ]] && chmod 600 "$RUNTIME_APP/.env"
[[ -f "$RUNTIME_APP/artifacts/user_credentials.key" ]] && chmod 600 "$RUNTIME_APP/artifacts/user_credentials.key"

cp "$SOURCE_PLIST" "$TARGET_PLIST"
chmod 600 "$TARGET_PLIST"

launchctl bootout "$SERVICE_DOMAIN" "$TARGET_PLIST" 2>/dev/null || true
launchctl bootstrap "$SERVICE_DOMAIN" "$TARGET_PLIST"
launchctl enable "$SERVICE_NAME"
launchctl kickstart -k "$SERVICE_NAME"

for attempt in {1..180}; do
  if /usr/bin/curl --silent --fail --max-time 2 http://localhost:3001/api/health >/dev/null; then
    print "Health check passed: http://localhost:3001/api/health"
    break
  fi
  if (( attempt == 180 )); then
    print -u2 "Service was installed but did not become healthy."
    print -u2 "Check: $RUNTIME_APP/artifacts/logs/production.error.log"
    exit 1
  fi
  sleep 1
done

print "Installed and started com.agenticai.trading"
print "Open: http://localhost:3001"
print "Status: launchctl print $SERVICE_NAME"
print "Logs: $RUNTIME_APP/artifacts/logs/production.error.log"
