FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV RTD_DB_PATH=/data/rtd.db
VOLUME /data
EXPOSE 8000
# RTD_API_TOKEN must be supplied at run time. Put a TLS-terminating proxy in front for https.
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
