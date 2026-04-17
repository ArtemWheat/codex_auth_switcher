#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="${HOME}/.cache/codex-auth-switcher"
LOG_FILE="${CACHE_DIR}/tray-launch.log"
LOCK_FILE="${CACHE_DIR}/tray.lock"
PID_FILE="${CACHE_DIR}/tray.pid"
PYTHON_BIN="/usr/bin/python3"
TRAY_ENTRY="${PROJECT_DIR}/tray_app.py"

mkdir -p "${CACHE_DIR}"

export PATH="${HOME}/.local/bin:${HOME}/.asdf/shims:${HOME}/.asdf/bin:${HOME}/.cargo/bin:${PATH}"

{
  echo "==== $(date --iso-8601=seconds) ===="
  echo "PWD(before)=$(pwd)"
  echo "PROJECT_DIR=${PROJECT_DIR}"
  echo "DISPLAY=${DISPLAY-}"
  echo "DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS-}"
  echo "XDG_CURRENT_DESKTOP=${XDG_CURRENT_DESKTOP-}"
  echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE-}"
  echo "PYTHON=$(command -v python3 || true)"
  echo "ABS_PYTHON=${PYTHON_BIN}"
  echo "PATH=${PATH}"
} >> "${LOG_FILE}"

cd "${PROJECT_DIR}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  {
    echo "launcher lock busy, exiting"
  } >> "${LOG_FILE}"
  exit 0
fi

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    {
      echo "tray already running via pidfile: ${EXISTING_PID}"
    } >> "${LOG_FILE}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

EXISTING_PID="$(pgrep -f -n "${TRAY_ENTRY}" || true)"
if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
  {
    echo "tray already running via pgrep: ${EXISTING_PID}"
  } >> "${LOG_FILE}"
  echo "${EXISTING_PID}" > "${PID_FILE}"
  exit 0
fi

export GDK_BACKEND="${GDK_BACKEND:-x11}"

{
  echo "launching tray"
  echo "GDK_BACKEND=${GDK_BACKEND}"
} >> "${LOG_FILE}"

if command -v notify-send >/dev/null 2>&1; then
  notify-send "Codex Auth Switcher" "Tray launcher started"
fi

if command -v setsid >/dev/null 2>&1; then
  setsid "${PYTHON_BIN}" "${TRAY_ENTRY}" >> "${LOG_FILE}" 2>&1 < /dev/null &
else
  nohup "${PYTHON_BIN}" "${TRAY_ENTRY}" >> "${LOG_FILE}" 2>&1 < /dev/null &
fi

echo "$!" > "${PID_FILE}"

{
  echo "spawned tray pid=$(cat "${PID_FILE}")"
} >> "${LOG_FILE}"

exit 0
