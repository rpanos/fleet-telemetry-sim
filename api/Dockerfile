# Fleet Ops: builds the React UI, then serves it + the JSON API from one container.
# Build context is the repo root (see docker-compose.yml).

# --- stage 1: build the UI ---------------------------------------------------
FROM node:20-alpine AS ui
WORKDIR /ui
COPY ui/package.json ./
RUN npm install --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# --- stage 2: the API (reuses the ingest package's command code) -------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY ingest/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ingest/fleet_ingest ./fleet_ingest
COPY --from=ui /ui/dist ./static
CMD ["uvicorn", "fleet_ingest.api:app", "--host", "0.0.0.0", "--port", "8000"]
