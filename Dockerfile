# syntax=docker/dockerfile:1
# LSI 불량 분석 어시스턴트 — 단일 이미지(FastAPI API + 빌드된 프론트 정적 서빙).
# 엔진: agno(OpenRouter). 자격증명(OPENROUTER_*/JIRA_*)은 런타임 env로 주입.

# --- 1) 프론트엔드 빌드 (Vite SPA) ---
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ ./
# 같은 오리진에서 서빙하므로 API 베이스를 비워 상대경로 사용(VITE_API="").
ENV VITE_API=""
RUN npm run build          # → /web/dist

# --- 2) 런타임 (Python / FastAPI) ---
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RVP_HOST=0.0.0.0 \
    RVP_PORT=8001
WORKDIR /app

COPY requirements-server.txt ./
RUN pip install -r requirements-server.txt

# 앱 코드 + KB(data) + 빌드된 프론트
COPY backend/ ./backend/
COPY src/ ./src/
COPY data/ ./data/
COPY --from=web /web/dist ./web/dist

EXPOSE 8001
# server.py 의 __main__ 이 RVP_HOST/PORT 로 uvicorn 기동. web/dist 존재 시 정적 서빙 활성.
CMD ["python", "backend/server.py"]
