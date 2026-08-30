# The app and the API ship as one image: FastAPI serves the built React bundle, so there is
# one origin, one url and no CORS.

FROM node:22-slim AS frontend
WORKDIR /frontend
# Dependencies first, so editing the app does not reinstall them on every build.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite.config.ts writes to ../backend/static, which is outside this stage's workdir.
RUN npm run build


FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /backend/static ./backend/static

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
