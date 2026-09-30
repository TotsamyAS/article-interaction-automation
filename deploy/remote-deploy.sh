#!/usr/bin/env bash
set -Eeuo pipefail

: "${RELEASE_SHA:?RELEASE_SHA is required}"
: "${APP_PUBLIC_IP:?APP_PUBLIC_IP is required}"

APP_ROOT=${APP_ROOT:-/opt/article-interaction-automation}
ENV_FILE=${ENV_FILE:-/etc/article-interaction-automation.env}
PROJECT_NAME=article-interaction-automation
RELEASE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [[ $(basename "$RELEASE_DIR") != "$RELEASE_SHA" ]]; then
  echo "Release directory does not match RELEASE_SHA: $RELEASE_DIR" >&2
  exit 1
fi
if [[ ! -r "$ENV_FILE" ]]; then
  echo "Missing readable runtime environment: $ENV_FILE" >&2
  exit 1
fi
for key in ROUTERAI_API_KEY INVITATION_SIGNING_KEY; do
  if ! grep -qE "^${key}=.+" "$ENV_FILE"; then
    echo "$ENV_FILE must contain a non-empty $key" >&2
    exit 1
  fi
done

python3 - "$APP_PUBLIC_IP" <<'PYIP'
import ipaddress
import sys

ip = ipaddress.ip_address(sys.argv[1])
if ip.version != 4:
    raise SystemExit("APP_PUBLIC_IP must be a public IPv4 address")
if not ip.is_global:
    raise SystemExit(f"APP_PUBLIC_IP must be globally routable, got {ip}")
PYIP

mkdir -p "${APP_ROOT}/releases"
cd "$RELEASE_DIR"
export APP_PUBLIC_IP

# public_base_url is deployment-specific. Keep the tracked development config unchanged
# and materialize the HTTPS IP origin only inside this immutable release directory.
python3 - "$APP_PUBLIC_IP" <<'PYCONFIG'
import json
import sys
from pathlib import Path

public_ip = sys.argv[1]
path = Path("backend/config.json")
payload = json.loads(path.read_text(encoding="utf-8"))
payload["public_base_url"] = f"https://{public_ip}"
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PYCONFIG

compose=(docker compose -p "$PROJECT_NAME" --env-file "$ENV_FILE" -f compose.yaml -f compose.prod.yaml)
"${compose[@]}" config >/dev/null
"${compose[@]}" build --pull backend frontend
"${compose[@]}" up -d --remove-orphans
"${compose[@]}" ps

healthy=0
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 5 "https://${APP_PUBLIC_IP}/health" >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
if [[ $healthy -ne 1 ]]; then
  echo "Deployment started, but https://${APP_PUBLIC_IP}/health did not become healthy." >&2
  echo "If this is the first deployment, inspect Caddy logs for Let's Encrypt IP-certificate issuance." >&2
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --tail=160 caddy frontend backend >&2 || true
  exit 1
fi

ln -sfn "$RELEASE_DIR" "${APP_ROOT}/current"

# Keep the current release and the two immediately preceding release directories.
mapfile -t stale < <(find "${APP_ROOT}/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr | awk 'NR > 3 {sub(/^[^ ]+ /, ""); print}')
if ((${#stale[@]})); then
  rm -rf -- "${stale[@]}"
fi

docker image prune -f >/dev/null
printf 'Deployed %s to https://%s\n' "$RELEASE_SHA" "$APP_PUBLIC_IP"
