# The public dashboard as a container, for the ECS Fargate service.
# Runtime dependencies (the dashboard group: Streamlit, the Snowflake
# connector with pandas, pydeck) come from uv.lock, hash-checked.
#   docker build --platform linux/arm64 -f dashboard/Dockerfile -t nyc311-dashboard .
# Credentials come at run time from the Secrets Manager entry named by
# NYC311_SNOWFLAKE_SECRET_NAME; nothing secret is in the image.
# The same official image from another registry when public.ecr.aws refuses
# anonymous pulls (CI passes mirror.gcr.io/library/python:3.13-slim).
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13-slim
FROM ${PYTHON_IMAGE}

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-default-groups --group dashboard --no-emit-project --output-file requirements.txt \
 && uv pip install --system --no-cache --require-hashes -r requirements.txt \
 && rm requirements.txt /usr/local/bin/uv \
 && useradd --create-home dashboard
# Pages import the Snowflake connection, two constants from ingestion and ml,
# and nothing else from those packages at run time.
COPY warehouse/__init__.py warehouse/connection.py warehouse/environment.py warehouse/
COPY ingestion/*.py ingestion/
COPY ml/__init__.py ml/examples.py ml/features.py ml/
COPY dashboard/ dashboard/
USER dashboard

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)"
ENTRYPOINT ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0", \
            "--server.headless=true", "--browser.gatherUsageStats=false"]
