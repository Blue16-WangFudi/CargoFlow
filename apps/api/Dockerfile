FROM python:3.13-slim

WORKDIR /app
COPY cargoflow_api ./cargoflow_api

EXPOSE 8000
CMD ["python", "-m", "cargoflow_api.server", "--host", "0.0.0.0", "--port", "8000"]
