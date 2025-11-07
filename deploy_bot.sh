#!/bin/bash

# ==============================
# Скрипт для установки OSINT Telegram бота
# ==============================

# Проверка прав root
if [[ $EUID -ne 0 ]]; then
   echo "Пожалуйста, запустите скрипт с правами root"
   exit 1
fi

echo "===================================="
echo " Установка OSINT Telegram бота"
echo "===================================="
echo ""

# 1️⃣ Создание пользователя tgbot
USERNAME="tgbot"
if id "$USERNAME" &>/dev/null; then
    echo "Пользователь $USERNAME уже существует"
else
    read -s -p "Введите пароль для нового пользователя tgbot: " USER_PASS
    echo
    adduser --gecos "" --disabled-password $USERNAME
    echo "$USERNAME:$USER_PASS" | chpasswd
    echo "Пользователь $USERNAME создан с указанным паролем"
fi

# 2️⃣ Установка необходимых пакетов
apt update
apt install -y python3 python3-venv python3-pip build-essential libffi-dev libssl-dev git

# 3️⃣ Создание структуры проекта
BOT_DIR="/home/$USERNAME/osint_bot"
mkdir -p $BOT_DIR/tasks
chown -R $USERNAME:$USERNAME $BOT_DIR

# 4️⃣ Клонирование репозитория с кодом бота
echo "📥 Скачивание кода из репозитория..."
sudo -u $USERNAME git clone https://github.com/Artem107/Osint-rgrtu.git $BOT_DIR

# 5️⃣ Запрос токена и ID администратора
echo ""
echo "- API_TOKEN: токен вашего Telegram-бота, полученный у BotFather."
read -p "Введите Telegram API_TOKEN: " API_TOKEN
echo ""
echo "- ADMIN_ID: ваш Telegram ID (можно узнать через @userinfobot)."
read -p "Введите ADMIN_ID (ваш Telegram ID): " ADMIN_ID
echo ""

# 6️⃣ Создание .env файла
cat > $BOT_DIR/.env <<EOL
API_TOKEN=$API_TOKEN
ADMIN_ID=$ADMIN_ID
EOL
chown $USERNAME:$USERNAME $BOT_DIR/.env

# 7️⃣ Создание виртуального окружения и установка зависимостей
sudo -u $USERNAME bash << EOF
cd $BOT_DIR
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install aiogram==3.2.0 aiosqlite openpyxl python-dotenv
EOF

# 8️⃣ Создание systemd сервиса
SERVICE_FILE="/etc/systemd/system/osintbot.service"
cat > $SERVICE_FILE <<EOL
[Unit]
Description=OSINT Telegram Bot
After=network.target

[Service]
User=$USERNAME
WorkingDirectory=$BOT_DIR
Environment="PATH=$BOT_DIR/.venv/bin"
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/.venv/bin/python $BOT_DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

# 9️⃣ Перезагрузка systemd и запуск сервиса
systemctl daemon-reload
systemctl enable osintbot.service
systemctl start osintbot.service

echo "===================================="
echo "✅ Установка завершена!"
echo "Статус бота: sudo systemctl status osintbot.service"
echo "Логи: sudo journalctl -u osintbot.service -f"
echo "Пользователь для входа на сервер: tgbot"
echo "Используйте пароль, который вы указали при установке"
echo "===================================="
