"""Snowflake connections for the pipeline and dashboard roles.

Credentials come from SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and
SNOWFLAKE_PRIVATE_KEY_PATH (local runs, e.g. `uv run --env-file .env`), or
from the Secrets Manager entry named by NYC311_SNOWFLAKE_SECRET_NAME
(JSON {"account", "user", "private_key"}) in deployed code.
"""

import json
import os

import snowflake.connector
from cryptography.hazmat.primitives import serialization
from snowflake.connector import SnowflakeConnection

REGION = "us-east-2"


def credentials_available() -> bool:
    return bool(os.environ.get("SNOWFLAKE_ACCOUNT") or os.environ.get("NYC311_SNOWFLAKE_SECRET_NAME"))


def connect(
    role: str, warehouse: str, database: str | None = None, statement_timeout_seconds: int | None = None
) -> SnowflakeConnection:
    account, user, key = _credentials()
    return snowflake.connector.connect(
        account=account,
        user=user,
        authenticator="SNOWFLAKE_JWT",
        private_key=key,
        role=role,
        warehouse=warehouse,
        database=database,
        session_parameters={
            "QUERY_TAG": "nyc311",
            "TIMEZONE": "UTC",
            **({"STATEMENT_TIMEOUT_IN_SECONDS": statement_timeout_seconds} if statement_timeout_seconds else {}),
        },
        login_timeout=60,
    )


def _credentials() -> tuple[str, str, bytes]:
    if os.environ.get("SNOWFLAKE_ACCOUNT"):
        with open(os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]), "rb") as f:
            pem = f.read()
        return os.environ["SNOWFLAKE_ACCOUNT"], os.environ["SNOWFLAKE_USER"], _der(pem)

    import boto3

    secret = boto3.client("secretsmanager", region_name=REGION).get_secret_value(
        SecretId=os.environ["NYC311_SNOWFLAKE_SECRET_NAME"]
    )
    value = json.loads(secret["SecretString"])
    return value["account"], value["user"], _der(value["private_key"].encode())


def _der(pem: bytes) -> bytes:
    key = serialization.load_pem_private_key(pem, password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
