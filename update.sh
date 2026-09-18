#!/usr/bin/env bash
# Обновление бота из GitHub.
#
# Запускается двумя способами:
#   вручную      bash /root/cre-bo/update.sh
#   таймером     раз в 10 минут, с ключом --quiet (молчит, когда нечего делать)
#
# Ничего не делает, если на GitHub нет нового коммита, поэтому его
# безопасно дёргать хоть каждую минуту.
set -euo pipefail

REPO=/root/cre-bo
BRANCH=main
SERVICE=crebot

QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1
say() { [[ $QUIET -eq 1 ]] || echo "$@"; }

cd "$REPO"
git fetch --quiet origin "$BRANCH"

local_rev=$(git rev-parse HEAD)
remote_rev=$(git rev-parse "origin/$BRANCH")

if [[ "$local_rev" == "$remote_rev" ]]; then
    say "Обновлений нет, версия ${local_rev:0:7}."
    exit 0
fi

echo "Обновление: ${local_rev:0:7} -> ${remote_rev:0:7}"

# Бот мог быть посреди разбора — например, идёт /reparse на всей базе.
# Перезапуск оборвал бы его, и материалы остались бы неразобранными.
# Ждём следующего срабатывания таймера. Отметку старше получаса считаем
# брошенной после сбоя, иначе одна неудача заблокировала бы выкатки
# навсегда.
if [[ -f "$REPO/.busy" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "$REPO/.busy" 2>/dev/null || echo 0) ))
    if (( age < 1800 )); then
        echo "Бот сейчас разбирает материалы (${age} с). Отложено до следующей проверки."
        exit 0
    fi
    echo "Отметка занятости старше получаса — считаю её брошенной."
fi

# Уборка перед обновлением. Раньше четыре файла источников лежали в
# репозитории плоско и копировались в sources/ вручную — эти копии git
# не отслеживает, и без уборки они не дали бы забрать новую раскладку.
# .env и база данных не отслеживаются и не трогаются.
git clean --quiet -fd sources 2>/dev/null || true
git reset --hard --quiet "origin/$BRANCH"

if ! git diff --quiet "$local_rev" "$remote_rev" -- requirements.txt 2>/dev/null; then
    echo "Изменились зависимости — доустанавливаю."
    pip install -r requirements.txt --quiet
fi

systemctl restart "$SERVICE"
sleep 3

if systemctl is-active --quiet "$SERVICE"; then
    echo "Готово. Бот работает, версия ${remote_rev:0:7}."
else
    echo "ВНИМАНИЕ: бот не поднялся после обновления."
    echo "Последние строки журнала:"
    journalctl -u "$SERVICE" -n 20 --no-pager
    exit 1
fi
