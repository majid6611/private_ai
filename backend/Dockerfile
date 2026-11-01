FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock
RUN groupadd --gid 10001 workspace && useradd --uid 10001 --gid 10001 --no-create-home workspace
RUN mkdir -p /data/documents && chown 10001:10001 /data/documents
COPY --chown=10001:10001 . .
USER 10001:10001
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
