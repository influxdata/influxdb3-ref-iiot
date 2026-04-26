FROM python:3.12-slim

WORKDIR /app

# Install just the runtime deps for the simulator (lighter than -e .[dev])
RUN pip install --no-cache-dir \
      "influxdb3-python>=0.7" \
      "httpx>=0.27"

COPY simulator /app/simulator
ENV PYTHONPATH=/app

CMD ["python", "-m", "simulator.main"]
