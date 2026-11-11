FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /connector
COPY mysql/requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY sdk/connector_sdk.py .
COPY mysql/mysql_connector ./mysql_connector
USER 10001:10001
CMD ["python", "-m", "mysql_connector"]

FROM runtime AS test
USER root
COPY mysql/requirements-test.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements-test.lock
COPY fixtures ./fixtures
COPY mysql/tests ./tests
USER 10001:10001
ENV PYTHONPATH=/connector
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
