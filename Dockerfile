FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY bridge.py eversense.py ./
RUN useradd -m app && mkdir /data && chown app /data
USER app
VOLUME /data
CMD ["python", "bridge.py"]
