FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FAULTLINE_STATIC_DIR=/app/frontend/dist \
    FAULTLINE_DB=/data/faultline.db \
    PORT=8000
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt && mkdir -p /data
COPY backend/ backend/
COPY --from=frontend-build /build/frontend/dist frontend/dist
WORKDIR /app/backend
CMD ["sh", "-c", "exec uvicorn faultline.api:app --host 0.0.0.0 --port ${PORT}"]
