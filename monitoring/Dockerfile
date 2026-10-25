# The monitor as a container-image Lambda: it needs the Snowflake connector,
# too large and native for a zip. Runtime dependencies come from uv.lock,
# hash-checked (the warehouse group: the connector; boto3 comes with it).
#   docker build --platform linux/arm64 -f monitoring/Dockerfile -t nyc311-monitor .
FROM public.ecr.aws/lambda/python:3.13

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-default-groups --group warehouse --no-emit-project --output-file requirements.txt \
 && uv pip install --system --no-cache --require-hashes -r requirements.txt \
 && rm requirements.txt /usr/local/bin/uv
COPY warehouse/__init__.py warehouse/connection.py warehouse/environment.py warehouse/
COPY monitoring/*.py monitoring/

CMD ["monitoring.lambda_handler.handler"]
