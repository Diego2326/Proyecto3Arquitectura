#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="proyecto3-iot-bridge"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/default/${SERVICE_NAME}"
DEFAULT_CLOUD_RUN_URL="https://proyecto3arquitectura-990072178406.northamerica-south1.run.app"
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -- bash "${BASH_SOURCE[0]}" "$@"
fi

SERVICE_USER="${SUDO_USER:-diego}"
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "No existe el usuario ${SERVICE_USER}. Ejecuta el instalador con sudo desde la sesión de ese usuario." >&2
  exit 1
fi

if [[ ! -f "${APP_ROOT}/orangepi/bridge.py" || ! -f "${APP_ROOT}/orangepi/requirements.txt" ]]; then
  echo "No encuentro orangepi/bridge.py y orangepi/requirements.txt en ${APP_ROOT}." >&2
  exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
  echo "Este instalador requiere una distribución Orange Pi basada en Debian/Ubuntu (apt-get)." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip

if ! getent group dialout >/dev/null 2>&1; then
  echo "No existe el grupo dialout; revisa el grupo asignado al puerto USB serial." >&2
  exit 1
fi
usermod -aG dialout "${SERVICE_USER}"

python3 -m venv "${APP_ROOT}/orangepi/.venv"
"${APP_ROOT}/orangepi/.venv/bin/python" -m pip install -r "${APP_ROOT}/orangepi/requirements.txt"

if [[ ! -t 0 ]]; then
  echo "Ejecuta este instalador en una terminal interactiva para introducir el token de Cloud Run." >&2
  exit 1
fi
printf 'Pega el DEVICE_TOKEN de Secret Manager (no se mostrará): '
IFS= read -r -s DEVICE_TOKEN
printf '\n'
if [[ ! "${DEVICE_TOKEN}" =~ ^[[:xdigit:]]{64}$ ]]; then
  unset DEVICE_TOKEN
  echo "El token debe tener 64 caracteres hexadecimales (openssl rand -hex 32)." >&2
  exit 1
fi

install -d -m 0755 /etc/default
umask 077
printf 'CLOUD_RUN_URL=%s\nDEVICE_TOKEN=%s\n' \
  "${DEFAULT_CLOUD_RUN_URL}" "${DEVICE_TOKEN}" > "${ENV_FILE}"
chown root:root "${ENV_FILE}"
chmod 0600 "${ENV_FILE}"
unset DEVICE_TOKEN

cat > "${SERVICE_FILE}" <<UNIT
[Unit]
Description=Arduino and Orange Pi WebSocket bridge
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
SupplementaryGroups=dialout
WorkingDirectory=${APP_ROOT}/orangepi
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_ROOT}/orangepi/.venv/bin/python ${APP_ROOT}/orangepi/bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

chmod 0644 "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

echo "Servicio instalado y habilitado: ${SERVICE_NAME}.service"
echo "Ver estado:  sudo systemctl status ${SERVICE_NAME}"
echo "Ver logs:    sudo journalctl -u ${SERVICE_NAME} -f"
