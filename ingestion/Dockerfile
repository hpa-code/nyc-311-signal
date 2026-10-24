# The collector as a container, for the one-off backfill ECS task.
#   docker build --platform linux/arm64 -f ingestion/Dockerfile -t nyc311-collector .
FROM public.ecr.aws/docker/library/python:3.13-slim

RUN pip install --no-cache-dir boto3==1.43.118 \
 && useradd --create-home collector
WORKDIR /app
COPY ingestion/*.py ingestion/
USER collector

ENTRYPOINT ["python", "-m", "ingestion.backfill"]
