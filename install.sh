#!/usr/bin/env bash
# codebot installer. Everything it installs lives inside this folder (tools/, models/, config/local.env); it never uses sudo and never touches a
# system-wide Ollama. It asks before every large download and before anything outside this folder (a symlink in ~/.local/bin, a user service).
#   ./install.sh                 interactive
#   ./install.sh --yes           accept every default (no symlink, no service unless also --link / --service)
#   ./install.sh --with-opencode also install opencode (only needed for `codebot plan`, the interview planner, and the legacy loop)
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"; cd "$HERE"
OLLAMA_VERSION="0.40.3"                     # the version codebot is tested with; a newer one may change structured-output behaviour
OLLAMA_SHA256_AMD64="ce6f8b671a699bf1ebafb28d60dbbabf4f37b30b499145fa308a8ba72de021d6"
OPENCODE_VERSION="1.19.0"
BASE_MODEL="ornith:9b"                      # DeepReinforce Ornith 1.0 (9B, MIT); config/Modelfile wraps it as codebot-model with a 32k context
YES=0; LINK=0; SERVICE=0; OPENCODE=0
for a in "$@"; do case "$a" in --yes|-y) YES=1;; --link) LINK=1;; --service) SERVICE=1;; --with-opencode) OPENCODE=1;; -h|--help) sed -n 2,7p "$0"; exit 0;;
  *) echo "unknown option $a"; exit 2;; esac; done
ask() { (( YES )) && return 0; read -r -p "$1 [Y/n] " r; [[ -z "$r" || "$r" =~ ^[Yy] ]]; }
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { echo "install: $*" >&2; exit 1; }

step "1/6 prerequisites"
missing=()
for t in python3 git curl jq zstd docker; do command -v "$t" >/dev/null || missing+=("$t"); done
if (( ${#missing[@]} )); then
  echo "missing: ${missing[*]}"
  echo "on Ubuntu/Debian: sudo apt install python3 git curl jq zstd   and Docker from https://docs.docker.com/engine/install/"
  die "install the missing tools, then run ./install.sh again"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "python3 3.10 or newer is required"
docker info >/dev/null 2>&1 || die "docker is installed but not usable by $(whoami): sudo usermod -aG docker $(whoami), then log out and back in"
echo "ok"

step "2/6 private Ollama $OLLAMA_VERSION in tools/ollama"
if [[ -x tools/ollama/bin/ollama ]]; then echo "already installed"
else
  case "$(uname -m)" in x86_64) arch=amd64;; aarch64|arm64) arch=arm64;; *) die "unsupported CPU architecture $(uname -m)";; esac
  url="https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/ollama-linux-$arch.tar.zst"
  ask "download $url (about 1.5 GB unpacked)?" || die "Ollama is required"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fL --progress-bar -o "$tmp/ollama.tar.zst" "$url"
  want="$OLLAMA_SHA256_AMD64"
  if [[ "$arch" != amd64 ]]; then want="$(curl -fsL "https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/sha256sum.txt" | awk -v f="./ollama-linux-$arch.tar.zst" '$2 == f {print $1}')"; fi
  got="$(sha256sum "$tmp/ollama.tar.zst" | cut -d' ' -f1)"
  [[ -n "$want" && "$got" == "$want" ]] || die "checksum mismatch for the Ollama download (got $got, expected ${want:-unknown}); nothing was installed"
  mkdir -p tools/ollama && tar --zstd -xf "$tmp/ollama.tar.zst" -C tools/ollama
  echo "installed $(tools/ollama/bin/ollama --version 2>/dev/null | tail -1)"
fi

step "3/6 your local settings (config/local.env, never committed)"
if [[ -f config/local.env ]]; then echo "config/local.env exists; left as it is"
else
  topic="codebot-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 16)"
  cat > config/local.env <<EOF
# Settings for THIS machine. Read after config/codebot.env, so anything here wins. Not tracked by git.
# Notifications are OFF until you turn them on. A ntfy topic works like a password: anyone who knows it can read your pushes and, with
# PHONE_CONTROL=1, pause or stop your runs and answer blocked questions (which the model then reads). Keep it private.
NTFY_ENABLED=0
PHONE_CONTROL=0
NTFY_TOPIC="$topic"
#NTFY_SERVER="https://ntfy.sh"
#SANDBOX_RO=                    # extra read-only mounts for the sandbox (the re mode needs your JDK and Ghidra here)
EOF
  echo "wrote config/local.env (notifications off; private topic $topic ready if you turn them on)"
fi

step "4/6 model"
export OLLAMA_HOST="127.0.0.1:$(sed -nE 's/^OLLAMA_PORT=([0-9]+).*/\1/p' config/codebot.env | head -1)" OLLAMA_MODELS="$HERE/models"
if ! curl -sf -m 2 "http://$OLLAMA_HOST/api/version" >/dev/null; then
  mkdir -p logs; setsid nohup tools/ollama/bin/ollama serve >> logs/ollama.log 2>&1 &
  for _ in $(seq 1 30); do curl -sf -m 2 "http://$OLLAMA_HOST/api/version" >/dev/null && break; sleep 1; done
fi
if tools/ollama/bin/ollama list | awk '{print $1}' | grep -qx "codebot-model:latest"; then echo "codebot-model already present"
else
  if ! tools/ollama/bin/ollama list | awk '{print $1}' | grep -qx "$BASE_MODEL"; then
    ask "download $BASE_MODEL (about 5.6 GB) into models/?" || die "a model is required"
    tools/ollama/bin/ollama pull "$BASE_MODEL"
  fi
  tools/ollama/bin/ollama create codebot-model -f config/Modelfile
fi
img="$(sed -nE 's/^SANDBOX_IMAGE="?([^" #]+).*/\1/p' config/codebot.env | head -1)"
if ! docker image inspect "$img" >/dev/null 2>&1; then
  ask "pull the sandbox base image $img (about 80 MB; codebot itself never pulls images)?" && docker pull "$img"
fi
tools/ollama/bin/ollama stop codebot-model >/dev/null 2>&1 || true

step "5/6 optional pieces"
if (( OPENCODE )) || { [[ ! -x tools/node_modules/.bin/opencode ]] && command -v npm >/dev/null && ! (( YES )) && ask "install opencode $OPENCODE_VERSION (only for 'codebot plan', the interview planner)?"; }; then
  command -v npm >/dev/null || die "npm is needed for opencode (sudo apt install npm)"
  (cd tools && npm install --no-fund --no-audit "opencode-ai@$OPENCODE_VERSION")
fi
if (( LINK )) || { ! (( YES )) && ! command -v codebot >/dev/null && ask "put a 'codebot' symlink in ~/.local/bin?"; }; then
  mkdir -p "$HOME/.local/bin" && ln -sf "$HERE/bin/codebot" "$HOME/.local/bin/codebot" && echo "linked ~/.local/bin/codebot"
fi
if (( SERVICE )) || { ! (( YES )) && command -v systemd-run >/dev/null && ask "install the watchdog (a systemd user timer that restarts loops that die)?"; }; then
  bin/codebot install-service
fi

step "6/6 check"
bin/codebot-doctor || true
cat <<'EOF'

Next:
  tests/run.sh                         harness tests against a fake model (about 6 min, no model needed)
  codebot bench --preflight            prove the context setup against the real model (about 5 min)
  codebot                              the guided menu; or see README.md "First project"
EOF
