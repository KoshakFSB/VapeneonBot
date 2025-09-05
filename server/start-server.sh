#!/bin/bash
# Установка зависимостей
pkg install python -y
pip install fastapi uvicorn

# Запуск сервера
python server.py