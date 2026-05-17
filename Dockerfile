FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache util-linux
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8088

CMD ["python", "-m", "app.service", "--config", "/data/config.json", "--host", "0.0.0.0", "--port", "8088"]
