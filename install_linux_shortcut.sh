#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/.local/share/applications"
DESKTOP_FILE="${APP_DIR}/com.maxim.codex-auth-tray.desktop"
OLD_DESKTOP_FILE="${APP_DIR}/codex-auth-switcher.desktop"
LEGACY_DESKTOP_FILE="${APP_DIR}/codex-auth-tray.desktop"
ICON_SOURCE="${PROJECT_DIR}/assets/icon.svg"
ICON_TARGET="${HOME}/.local/share/icons/codex-auth-tray-main.svg"

mkdir -p "${APP_DIR}"
mkdir -p "$(dirname "${ICON_TARGET}")"

cp "${ICON_SOURCE}" "${ICON_TARGET}"
rm -f "${HOME}/.local/share/icons/codex-auth-switcher.svg"
rm -f "${OLD_DESKTOP_FILE}"
rm -f "${LEGACY_DESKTOP_FILE}"

cat > "${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Codex Auth Tray
Comment=Codex account switcher in the top panel
Exec=${PROJECT_DIR}/launch_tray.sh
Path=${PROJECT_DIR}
Icon=${ICON_TARGET}
Terminal=false
Categories=Utility;
StartupNotify=false
DBusActivatable=false
EOF

chmod 644 "${DESKTOP_FILE}"
update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true

printf 'Installed: %s\n' "${DESKTOP_FILE}"
