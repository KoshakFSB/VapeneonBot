FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install pytz

COPY . .

CMD ["python", "main.py"]
