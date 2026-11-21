FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/main.py ./
COPY backend/scripts/build_knowledge_index.py ./scripts/
COPY docs/knowledge /app/docs/knowledge

# 空命名卷首次挂载时会继承这个目录，供进程写入 SQLite 和索引文件。
RUN useradd --uid 10001 --no-create-home --home-dir /app/backend ruoyi \
    && install -d -o ruoyi -g ruoyi /app/backend/data
USER ruoyi

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
