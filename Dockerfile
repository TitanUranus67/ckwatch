FROM python:3-slim

WORKDIR /srv/ckwatch

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config.toml .

ENV CKWATCH_LOG_DIR=/ckpool/logs \
    CKWATCH_DB=/data/ckwatch.db \
    CKWATCH_PORT=8080 \
    CKWATCH_HOST=0.0.0.0

EXPOSE 8080
VOLUME ["/data"]

CMD ["python", "-m", "app.web"]
