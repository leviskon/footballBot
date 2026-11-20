#!/usr/bin/env bash
# Установка или обновление бота на сервере с Ubuntu/Debian.
#
# Обычно его запускает deploy.bat с компьютера. Вручную, на сервере:
#   bash deploy/setup.sh [папка с файлами бота]
#
# Бот ставится в ~/footbalBot и работает как сервис systemd «footbalbot»:
# стартует при загрузке сервера и перезапускается при сбоях.
# База (data/) и логи (logs/) при обновлении сохраняются.
set -euo pipefail

SERVICE="footbalbot"
RUN_USER="$(id -un)"
SRC="$(cd "${1:-$(dirname "$0")/..}" && pwd)"
APP_DIR="${APP_DIR:-$HOME/footbalBot}"
mkdir -p "$APP_DIR"
APP_DIR="$(cd "$APP_DIR" && pwd)"
VENV="$APP_DIR/.venv"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf '\n[ошибка] %s\n' "$*" >&2; exit 1; }

python_ok() {  # Python 3.11+ with venv support (Debian ships ensurepip in pythonX.Y-venv)
    "$1" -c 'import sys, ensurepip; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1
}
apt_update() {
    local attempt
    for attempt in 1 2 3 4 5; do
        sudo apt-get -q -o DPkg::Lock::Timeout=600 update && return 0
        sleep 15
    done
    return 1
}
apt_install() {
    sudo env DEBIAN_FRONTEND=noninteractive \
        apt-get -q -y -o DPkg::Lock::Timeout=600 install "$@"
}

[ -f "$SRC/bot/__main__.py" ] || fail "В папке $SRC нет файлов бота"
# GitHub Actions присылает только код, без .env: берём тот, что уже лежит на сервере.
if [ ! -f "$SRC/.env" ] && [ "$SRC" != "$APP_DIR" ] && [ -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env" "$SRC/.env"
fi
[ -f "$SRC/.env" ] || fail "В папке $SRC нет файла .env с токеном"
command -v systemctl >/dev/null || fail "На сервере нет systemd — нужен Ubuntu или Debian"
sudo -n true 2>/dev/null || fail "Пользователю $RUN_USER нужен sudo без пароля (на Oracle Cloud он есть у ubuntu)"

# ----------------------------------------------------------------- Python
step "Проверяю Python"
PY=python3
if ! python_ok python3; then
    # On the very first boot cloud-init may still be installing updates.
    if command -v cloud-init >/dev/null; then sudo cloud-init status --wait >/dev/null 2>&1 || true; fi
    apt_update || fail "Не удалось обновить список пакетов (apt-get update)"
    apt_install python3 python3-venv
    if ! python_ok python3; then
        apt_install python3.11 python3.11-venv \
            || fail "Нужен Python 3.11 или новее. Пересоздай сервер с образом Ubuntu 24.04"
        PY=python3.11
    fi
fi
"$PY" --version

# ----------------------------------------------------------- dependencies
step "Ставлю зависимости (в первый раз — около минуты)"
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    rm -rf "$VENV"
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$SRC/requirements.txt"

# The new files are checked before they replace the running bot's ones.
step "Проверяю настройки .env"
(cd "$SRC" && "$VENV/bin/python" - <<'PYCODE'
import os
import sys

from bot.config import ConfigError, load_config

try:
    config = load_config()
except ConfigError as exc:
    sys.exit(f"Ошибка в .env: {exc}")
print(f"Токен на месте. Часовой пояс: {config.tz_name}")
if not os.environ.get("TIMEZONE"):
    print("Внимание: в .env не указан TIMEZONE — время будет по UTC. Пример: TIMEZONE=Asia/Bishkek")
PYCODE
) || fail "Исправь .env на компьютере и запусти deploy.bat ещё раз (бот на сервере не тронут)"

# ------------------------------------------------------------------ files
step "Копирую файлы в $APP_DIR"
if [ "$SRC" != "$APP_DIR" ]; then
    rm -rf "$APP_DIR/bot" "$APP_DIR/deploy"
    cp -r "$SRC/bot" "$SRC/deploy" "$APP_DIR/"
    cp "$SRC/main.py" "$SRC/requirements.txt" "$APP_DIR/"
    cp "$SRC/.env" "$APP_DIR/.env"
fi
chmod 600 "$APP_DIR/.env"

# ---------------------------------------------------------------- systemd
step "Настраиваю автозапуск"
sudo tee "/etc/systemd/system/$SERVICE.service" >/dev/null <<EOF
[Unit]
Description=Football news Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$VENV/bin/python -m bot
Restart=always
RestartSec=10
# 2 = error in .env, 3 = Telegram rejected the token: restarting will not help
RestartPreventExitStatus=2 3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --quiet "$SERVICE"
STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
sudo systemctl restart "$SERVICE"

step "Проверяю, что бот запустился"
sleep 10
sudo journalctl -u "$SERVICE" --since "$STARTED_AT" --no-pager -o cat | tail -n 20 || true
systemctl is-active --quiet "$SERVICE" || fail "Бот не запустился — причина в логе выше"

step "Готово! Бот работает на сервере"
cat <<EOF
Команды на сервере:
  sudo journalctl -u $SERVICE -f      логи в реальном времени (выход — Ctrl+C)
  sudo systemctl restart $SERVICE     перезапустить
  sudo systemctl stop $SERVICE        остановить
EOF
