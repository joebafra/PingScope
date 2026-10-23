FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fping mtr-tiny \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pingscope ./pingscope
COPY examples ./examples
ENV PINGSCOPE_DB=/data/pingscope.db \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "pingscope"]
