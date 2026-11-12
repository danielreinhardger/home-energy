#!/usr/bin/env bash
# Deploy to the NAS: test first, copy, rebuild, then check the service really runs.
#
#   ./deploy.sh            tests → copy → rebuild → health check
#   ./deploy.sh --prune    also delete files on the NAS that no longer exist here
#   ./deploy.sh --no-tests skip the tests (only when you know why)
#
# Stops at the first failing step; nothing is copied when a test fails.
# Where to: deploy.env beside this script (git-ignored, see deploy.env.example).
set -euo pipefail
cd "$(dirname "$0")"

[ -f deploy.env ] && . ./deploy.env
NAS="${NAS:-nas}"                                  # ssh host (key auth, see ~/.ssh/config)
: "${DIR:?set DIR (the folder on the NAS) in deploy.env}"
: "${URL:?set URL (the dashboard, e.g. http://<nas>:8085) in deploy.env}"
SHIPPED=(Dockerfile compose.yaml .dockerignore run.py deploy.sh README.md homeenergy web docs tests)
CODE_DIRS=(homeenergy homeenergy/devices web tests docs)   # checked for leftovers on the NAS

prune=0 tests=1
for arg in "$@"; do
  case "$arg" in
    --prune) prune=1 ;;
    --no-tests) tests=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
step() { printf '\n== %s\n' "$*"; }

if [ "$tests" = 1 ]; then
  step "Tests"
  if ! out=$(python -m unittest discover -s tests 2>&1); then
    echo "$out" | tail -30; echo "tests failed – nothing deployed" >&2; exit 1
  fi
  echo "$out" | tail -3
  if command -v node >/dev/null; then
    for f in web/*.js; do node --check "$f" || { echo "syntax error in $f – nothing deployed" >&2; exit 1; }; done
    echo "web modules: syntax ok"
  fi
fi

step "Leftovers on the NAS"
local_list=$(for d in "${CODE_DIRS[@]}"; do find "$d" -maxdepth 1 -type f ! -path '*/__pycache__/*' 2>/dev/null; done | sort)
remote_list=$(ssh "$NAS" "cd $DIR && for d in ${CODE_DIRS[*]}; do find \$d -maxdepth 1 -type f 2>/dev/null; done | grep -v __pycache__ | sort")
stale=$(comm -13 <(echo "$local_list") <(echo "$remote_list") || true)
if [ -z "$stale" ]; then echo "none"
elif [ "$prune" = 1 ]; then echo "$stale" | sed 's/^/  delete /'; ssh "$NAS" "cd $DIR && rm -f $(echo "$stale" | tr '\n' ' ')"
else echo "$stale" | sed 's/^/  /'; echo "(kept – run with --prune to delete them)"
fi

step "Copy"
tar --exclude=__pycache__ --exclude=config.json --exclude=data -cf - "${SHIPPED[@]}" | ssh "$NAS" "cd $DIR && tar -xf -"
echo "copied"

step "Rebuild and restart"
ssh "$NAS" "cd $DIR && sudo -n docker compose up -d --build 2>&1 | tail -2"

step "Health check"
# Wait for the first control cycle and a first read of every device, at most 2 minutes.
for i in $(seq 1 24); do
  perf=$(curl -sf --compressed -m 5 "$URL/api/perf" 2>/dev/null || true)
  # ready: a control cycle ran and every device was read at least once
  if echo "$perf" | python -c "import json,sys; p=json.load(sys.stdin); sys.exit(0 if p['control_ms'] is not None and all(d['reads'] for d in p['devices'].values()) else 1)" 2>/dev/null; then break; fi
  perf=""; sleep 5
done
if [ -z "$perf" ]; then
  echo "no control cycle within 2 minutes – check: ssh $NAS 'sudo -n docker logs --tail 50 home-energy'" >&2
  exit 1
fi
echo "$perf" | python -c "
import json, sys
p = json.load(sys.stdin)
bad = 0
for name, d in p['devices'].items():
    ok = d['failures'] == 0 or d['reads'] > d['failures']
    bad += not ok
    print(f\"  {name:8} {'ok ' if ok else 'ERR'} read {d['read_ms'] or 0:.0f} ms, {d['reads']} reads, {d['failures']} failures\")
print(f\"  control  ok  {p['control_ms']} ms\")
sys.exit(1 if bad else 0)
" || { echo "a device is not readable – see the dashboard status line" >&2; exit 1; }
# the page's own data: a read-model error shows up only here, not in /api/perf
for api in state meta; do
  code=$(curl -s --compressed -m 15 -o /dev/null -w '%{http_code}' "$URL/api/$api" || true)
  [ "$code" = 200 ] || { echo "/api/$api answers $code – check: ssh $NAS 'sudo -n docker logs --tail 50 home-energy'" >&2; exit 1; }
done
echo "  page     ok  /api/state, /api/meta"
echo
echo "deployed: $URL"
