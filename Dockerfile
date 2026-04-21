FROM python:3.11-slim

WORKDIR /app

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходники
COPY main.py .
COPY web.py .
COPY dashboard.html .

# Директории для данных и логов
RUN mkdir -p data logs

# Веб-панель на порту 8080
EXPOSE 8080

CMD ["python", "main.py"]
