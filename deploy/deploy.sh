#!/usr/bin/env bash
#
# Deploy E-Attendance on the production host. Safe to run repeatedly.
#
#   cd ~/eattendance && ./deploy/deploy.sh
#
# Installs dependencies, applies migrations, collects static files, installs
# the systemd units, restarts the service, and then verifies the result -
# because the failure that cost the most time on this box was a deploy that
# *looked* fine while an older gunicorn served the traffic.
#
# Needs sudo for the systemd and /var/www steps; it will prompt.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
PY="$REPO/.venv/bin/python"
BIND_HOST=127.0.0.1
BIND_PORT=8001

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "no virtualenv at $REPO/.venv - create it with: python3 -m venv .venv"

# Only ever look at gunicorn processes belonging to THIS project. The host also
# runs deepit.service, an unrelated Django site out of /srv/deepit on port 8000,
# and matching on "gunicorn" alone counts its processes too - which produced a
# confusing false alarm about a competing instance. Match on this repo's path.
ours() { pgrep -f "gunicorn" 2>/dev/null | xargs -r ps -o pid=,cmd= -p 2>/dev/null | grep -F "$REPO" || true; }
ours_pids() { ours | awk '{print $1}'; }
ours_count() { ours | grep -c . || true; }

# ---------------------------------------------------------------------------
say "Checking for stale gunicorn processes"
# The trap this catches: an older unit, or a hand-started process from THIS
# project, still bound to the port. The deploy appears to succeed while the old
# code serves every request - which is exactly what happened here, and it
# silently halved a benchmark before anyone noticed.
#
# Other gunicorns on the host are none of our business; see ours() above.
if ours | grep -q -- "--workers\|--access-logfile"; then
    warn "a gunicorn from this project is running WITHOUT deploy/gunicorn.conf.py:"
    ours | grep -- "--workers\|--access-logfile" | sed 's/^/    /'
    warn "systemctl stop it (and disable it) before continuing, or it will fight for port $BIND_PORT"
fi

# ---------------------------------------------------------------------------
say "Installing Python dependencies"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt

say "Applying database migrations"
"$PY" manage.py migrate --noinput

say "Collecting static files"
STATIC_ROOT_DIR="${STATIC_ROOT:-/var/www/attendance.thedeepit.com}"
if [ ! -d "$STATIC_ROOT_DIR" ]; then
    warn "$STATIC_ROOT_DIR does not exist; creating it"
    sudo mkdir -p "$STATIC_ROOT_DIR"
    sudo chown -R "$USER":www-data "$STATIC_ROOT_DIR"
    sudo chmod -R 755 "$STATIC_ROOT_DIR"
fi
STATIC_ROOT="$STATIC_ROOT_DIR" "$PY" manage.py collectstatic --noinput

# WhiteNoise builds its file index once at start-up (WHITENOISE_AUTOREFRESH is
# off in production, deliberately - it otherwise stats the filesystem on every
# request). So collectstatic MUST happen before the service restarts, or the
# running workers keep serving from a file list that predates the new assets
# and answer with Django's HTML 404 - which the browser then rejects with
# "Refused to apply style ... MIME type ('text/html')". The restart below is
# what publishes new CSS, not collectstatic on its own.

say "Pre-compiling bytecode"
# Workers then start from cached .pyc instead of compiling on first import.
# Only affects startup and restart time, but with eight workers that adds up.
"$PY" -m compileall -q "$REPO/core" "$REPO/devices" "$REPO/attendance" \
    "$REPO/leaves" "$REPO/travel_orders" "$REPO/ehajiri" >/dev/null || true

say "Updating SQLite query planner statistics"
# ANALYZE lets SQLite choose indexes from real table statistics rather than
# guesses. Cheap on a database this size, and worth doing after migrations.
"$PY" - <<'PYEOF'
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ehajiri.settings')
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute('ANALYZE;')
    cursor.execute('PRAGMA optimize;')
    cursor.execute('PRAGMA journal_mode;')
    print('  journal_mode =', cursor.fetchone()[0])
PYEOF

# ---------------------------------------------------------------------------
say "Installing systemd units"
sudo cp "$REPO/deploy/systemd/cpu-performance.service" /etc/systemd/system/
sudo cp "$REPO/deploy/systemd/attendance.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cpu-performance
sudo systemctl enable attendance
sudo systemctl restart attendance

say "Verifying"
sleep 4

fail=0

freqs=$(cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq 2>/dev/null | sort -u | tr '\n' ' ' || echo 'n/a')
echo "  CPU frequencies : $freqs"
case "$freqs" in
    *400000*) warn "cores still parked at 400 MHz - governor did not apply"; fail=1 ;;
esac

# Count only this project's processes - the host runs other Django sites.
workers=$(ours_count)
expected=$(( ${WEB_CONCURRENCY:-8} + 1 ))
echo "  gunicorn procs  : $workers (expected $expected: master + workers)"
[ "$workers" -eq "$expected" ] || { warn "unexpected process count for this project - check for a competing instance"; fail=1; }

if ours | grep -q -- "--workers"; then
    warn "a process from this project is still running the OLD command line (--workers ...)"
    fail=1
fi

code=$(curl -s -o /dev/null -w '%{http_code}' "http://$BIND_HOST:$BIND_PORT/healthz/" || echo 000)
echo "  /healthz/       : HTTP $code"
[ "$code" = "200" ] || { warn "health check failed - see: journalctl -u attendance -n 50"; fail=1; }

# A running service and a service that survives a reboot are different things,
# and the difference is invisible until the box reboots at 3am. Check it here
# rather than finding out then.
for unit in attendance cpu-performance; do
    state=$(systemctl is-enabled "$unit" 2>/dev/null || echo unknown)
    echo "  $unit boot start: $state"
    [ "$state" = "enabled" ] || { warn "$unit will NOT start at boot - fix with: sudo systemctl enable $unit"; fail=1; }
done

rss=$(ours_pids | xargs -r ps -o rss= -p 2>/dev/null | awk '{sum+=$1} END {printf "%.0f", sum/1024}')
echo "  gunicorn RSS    : ${rss:-?} MB total (sums shared pages, so an overestimate)"
free -m | awk '/^Mem:/ {printf "  memory          : %s MB used, %s MB available of %s MB\n", $3, $7, $2}'

if [ "$fail" -eq 0 ]; then
    printf '\n\033[1;32m==> Deploy OK\033[0m\n'
    echo "    Benchmark with: ab -k -n 20000 -c 200 http://$BIND_HOST:$BIND_PORT/healthz/"
else
    printf '\n\033[1;31m==> Deployed, but verification found problems (see warnings above)\033[0m\n'
    exit 1
fi
