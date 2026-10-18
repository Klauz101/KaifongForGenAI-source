#!/usr/bin/env bash
# Kaifong quickstart: from a fresh clone to a running demo (mock mode, no API keys needed).
#
#   ./quickstart.sh                 set up, seed demo data, start backend + frontend
#   ./quickstart.sh --install-node  also download a project-local Node LTS if Node is missing (no system changes)
#   ./quickstart.sh --check         only check prerequisites; change nothing
#   ./quickstart.sh --no-run        set up and seed, but do not start the servers
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PATH="$PWD/.node/bin:$PATH"   # use the project-local Node when present

INSTALL_NODE=0; CHECK=0; RUN=1
for arg in "$@"; do
  case "$arg" in
    --install-node) INSTALL_NODE=1 ;;
    --check) CHECK=1 ;;
    --no-run) RUN=0 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

say "Checking prerequisites"
command -v python3 >/dev/null || fail "python3 not found. Install Python 3.11 or newer."
python3 - <<'PY' || fail "Python 3.11 or newer is required."
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY
command -v make >/dev/null || fail "make not found (macOS: xcode-select --install)."
echo "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'), make OK"

if command -v npm >/dev/null && command -v node >/dev/null; then
  echo "node $(node --version) OK"
elif [ "$INSTALL_NODE" = 1 ] && [ "$CHECK" = 0 ]; then
  say "Node not found: downloading a project-local Node LTS into ./.node (via nodeenv, official nodejs.org build)"
  [ -x backend/.venv/bin/pip ] || python3 -m venv backend/.venv
  make node
else
  [ "$CHECK" = 1 ] && echo "Node: MISSING (rerun with --install-node, or install Node 20+)" && exit 1
  fail "Node 20+ not found. Install it, or rerun with:  ./quickstart.sh --install-node"
fi
command -v ffmpeg >/dev/null && echo "ffmpeg OK" || echo "ffmpeg not found: fine for mock mode (typed calls); needed only for real speech providers."

[ "$CHECK" = 1 ] && { say "All required prerequisites present."; exit 0; }

say "Installing dependencies (make setup)"
make setup

say "Seeding synthetic demo data (make seed)"
make seed

cat <<'EOF'

Ready. Sign in at http://localhost:5173 (password for all: demo1234)
  worker@demo.kaifong   social worker: triage, calls, alerts
  admin@demo.kaifong    admin: Demo control, Metrics, Audit
  family2@demo.kaifong  family: weekly digest only
Open a person's page and press "Open phone simulator" to try a call (type your replies).
The 3-minute demo script is in README.md. Stop with Ctrl+C.

EOF

if [ "$RUN" = 1 ]; then
  say "Starting backend (http://localhost:8000) and frontend (http://localhost:5173)"
  exec make dev
else
  echo "Skipped starting servers (--no-run). Start later with:  make dev"
fi
