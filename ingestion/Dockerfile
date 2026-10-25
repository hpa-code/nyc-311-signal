# The collector as a container, for the one-off backfill ECS task. Runtime
# dependencies (boto3 and what it needs) come from uv.lock, hash-checked.
#   docker build --platform linux/arm64 -f ingestion/Dockerfile -t nyc311-collector .
# The same official image from another registry when public.ecr.aws refuses
# anonymous pulls (CI passes mirror.gcr.io/library/python:3.13-slim).
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13-slim
FROM ${PYTHON_IMAGE}

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-default-groups --no-emit-project --output-file requirements.txt \
 && uv pip install --system --no-cache --require-hashes -r requirements.txt \
 && rm requirements.txt /usr/local/bin/uv \
 && useradd --create-home collector
COPY ingestion/*.py ingestion/
USER collector

ENTRYPOINT ["python", "-m", "ingestion.backfill"]
