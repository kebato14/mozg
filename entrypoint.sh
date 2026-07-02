#!/bin/sh
set -e

# Разворачиваем секреты из base64 переменных окружения
mkdir -p /app/secrets /app/data

if [ -n "$GOOGLE_CREDENTIALS_B64" ]; then
    echo "$GOOGLE_CREDENTIALS_B64" | base64 -d > /app/secrets/google_credentials.json
    echo "✓ google_credentials.json восстановлен"
fi

if [ -n "$GOOGLE_TOKEN_B64" ]; then
    echo "$GOOGLE_TOKEN_B64" | base64 -d > /app/secrets/token.json
    echo "✓ token.json восстановлен"
fi

exec python3 bot.py
