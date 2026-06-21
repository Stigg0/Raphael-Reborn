#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
NATS_BOX_IMAGE="${NATS_BOX_IMAGE:-docker.io/natsio/nats-box:0.16.0}"
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-podman}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}" >&2
  exit 1
fi

read_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    return 1
  fi
  printf '%s' "${line#*=}" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

hash_password() {
  local password="$1"
  "${CONTAINER_RUNTIME}" run --rm "${NATS_BOX_IMAGE}" nats server passwd -p "${password}" | tail -n 1
}

upsert_env() {
  local key="$1"
  local value="$2"
  ENV_FILE="${ENV_FILE}" KEY="${key}" VALUE="${value}" python - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["ENV_FILE"])
key = os.environ["KEY"]
value = os.environ["VALUE"]
line = f"{key}={value}"
lines = path.read_text().splitlines()
for idx, existing in enumerate(lines):
    if existing.startswith(f"{key}="):
        lines[idx] = line
        break
else:
    lines.append(line)
path.write_text("\n".join(lines) + "\n")
PY
}

for name in BOT WORKER API; do
  password_key="NATS_${name}_PASSWORD"
  hash_key="NATS_${name}_PASSWORD_BCRYPT"
  password="$(read_env "${password_key}")" || {
    echo "Missing ${password_key} in ${ENV_FILE}" >&2
    exit 1
  }
  hash="$(hash_password "${password}")"
  upsert_env "${hash_key}" "${hash}"
  echo "Updated ${hash_key}"
done
