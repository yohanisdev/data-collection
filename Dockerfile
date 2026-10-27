FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY --from=frontend /app/frontend/dist ./frontend-dist
RUN useradd --system --create-home appuser && chown -R appuser:appuser /app
USER appuser
ENV FLASK_APP=backend
ENV APP_ENV=production
EXPOSE 5000
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:5000", "backend:create_app()"]
