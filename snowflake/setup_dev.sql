-- One-time, account-level setup for the dev environment (NYC311_DEV).
-- Run once as ACCOUNTADMIN in a Snowflake worksheet ("Run All"). Safe to re-run.
-- Everything inside the database is created by reviewed migrations
-- (snowflake/migrations/), applied by the pipeline role, not here.

USE ROLE ACCOUNTADMIN;

-- One small shared warehouse; suspends after 60 s idle, kills runaway queries.
CREATE WAREHOUSE IF NOT EXISTS NYC311_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  STATEMENT_TIMEOUT_IN_SECONDS = 600;

-- Pipeline role owns the dev database and everything in it.
-- Dashboard role only ever reads APP views (granted by migrations).
CREATE ROLE IF NOT EXISTS NYC311_DEV_PIPELINE;
CREATE ROLE IF NOT EXISTS NYC311_DEV_DASHBOARD;
GRANT ROLE NYC311_DEV_PIPELINE TO ROLE SYSADMIN;
GRANT ROLE NYC311_DEV_DASHBOARD TO ROLE SYSADMIN;

CREATE DATABASE IF NOT EXISTS NYC311_DEV;
GRANT OWNERSHIP ON DATABASE NYC311_DEV TO ROLE NYC311_DEV_PIPELINE COPY CURRENT GRANTS;

-- Seam 2 tests rebuild this database's schemas from scratch on every run.
-- (No account-wide CREATE DATABASE for the pipeline role.)
CREATE TRANSIENT DATABASE IF NOT EXISTS NYC311_DEV_TEST DATA_RETENTION_TIME_IN_DAYS = 0;
GRANT OWNERSHIP ON DATABASE NYC311_DEV_TEST TO ROLE NYC311_DEV_PIPELINE COPY CURRENT GRANTS;
REVOKE CREATE DATABASE ON ACCOUNT FROM ROLE NYC311_DEV_PIPELINE;

GRANT USAGE ON WAREHOUSE NYC311_WH TO ROLE NYC311_DEV_PIPELINE;
GRANT USAGE ON WAREHOUSE NYC311_WH TO ROLE NYC311_DEV_DASHBOARD;

-- Read-only access to the dev bucket's data/ and manifests/ prefixes, through
-- the AWS role dev-nyc311-snowflake-read (Terraform). The role's trust policy
-- needs STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID from
-- DESC INTEGRATION NYC311_DEV_S3.
CREATE STORAGE INTEGRATION IF NOT EXISTS NYC311_DEV_S3
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = 'arn:aws:iam::778031418236:role/dev-nyc311-snowflake-read'
  STORAGE_ALLOWED_LOCATIONS = (
    's3://dev-nyc311-observations-778031418236/data/',
    's3://dev-nyc311-observations-778031418236/manifests/'
  );
GRANT USAGE ON INTEGRATION NYC311_DEV_S3 TO ROLE NYC311_DEV_PIPELINE;

-- The service user gets both roles. No default secondary roles, so a session
-- opened as NYC311_DEV_DASHBOARD really is limited to that role.
GRANT ROLE NYC311_DEV_PIPELINE TO USER NYC311_SVC;
GRANT ROLE NYC311_DEV_DASHBOARD TO USER NYC311_SVC;
ALTER USER NYC311_SVC SET
  DEFAULT_ROLE = NYC311_DEV_PIPELINE
  DEFAULT_WAREHOUSE = NYC311_WH
  DEFAULT_SECONDARY_ROLES = ();
