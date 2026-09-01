FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NETPULSE_FRONTEND_DIR=/app/frontend \
    NETPULSE_DATABASE_URL=sqlite:////data/netpulse.db

WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir "/app/backend[ai]" \
    && addgroup --system netpulse \
    && adduser --system --ingroup netpulse netpulse \
    && mkdir -p /data /app/frontend \
    && chown -R netpulse:netpulse /data /app
COPY --from=frontend-build --chown=netpulse:netpulse /build/dist/ /app/frontend/

USER netpulse
WORKDIR /app/backend
EXPOSE 8000
CMD ["netpulse", "serve", "--host", "0.0.0.0", "--port", "8000"]
