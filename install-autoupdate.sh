#!/usr/bin/env bash
# Ставит автообновление: раз в 10 минут сервер сам забирает новый код
# с GitHub и перезапускает бот, если что-то изменилось.
#
# Запускается ОДИН раз:  bash /root/cre-bo/install-autoupdate.sh
set -euo pipefail

chmod +x /root/cre-bo/update.sh

cat > /etc/systemd/system/crebot-update.service <<'UNIT'
[Unit]
Description=Обновление бота CRE из GitHub
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/root/cre-bo
ExecStart=/bin/bash /root/cre-bo/update.sh --quiet
UNIT

cat > /etc/systemd/system/crebot-update.timer <<'UNIT'
[Unit]
Description=Проверка обновлений бота CRE каждые 10 минут

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Unit=crebot-update.service

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now crebot-update.timer

echo
echo "Автообновление установлено."
echo "Проверка раз в 10 минут. Журнал: journalctl -u crebot-update -n 30"
echo
systemctl list-timers crebot-update.timer --no-pager
