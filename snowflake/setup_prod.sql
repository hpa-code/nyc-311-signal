-- One-time, account-level setup for production (NYC311). Run once as
-- ACCOUNTADMIN in a Snowflake worksheet ("Run All"), after setup_dev.sql
-- (which creates the shared NYC311_WH warehouse). Safe to re-run.
-- Everything inside the database is created by reviewed migrations
-- (NYC311_ENV=prod python -m warehouse.migrate), applied by the pipeline role.

USE ROLE ACCOUNTADMIN;

CREATE ROLE IF NOT EXISTS NYC311_PROD_PIPELINE;
CREATE ROLE IF NOT EXISTS NYC311_PROD_DASHBOARD;
GRANT ROLE NYC311_PROD_PIPELINE TO ROLE SYSADMIN;
GRANT ROLE NYC311_PROD_DASHBOARD TO ROLE SYSADMIN;

CREATE DATABASE IF NOT EXISTS NYC311;
GRANT OWNERSHIP ON DATABASE NYC311 TO ROLE NYC311_PROD_PIPELINE COPY CURRENT GRANTS;

GRANT USAGE ON WAREHOUSE NYC311_WH TO ROLE NYC311_PROD_PIPELINE;
GRANT USAGE ON WAREHOUSE NYC311_WH TO ROLE NYC311_PROD_DASHBOARD;
GRANT EXECUTE TASK ON ACCOUNT TO ROLE NYC311_PROD_PIPELINE;

-- Read-only access to the prod bucket's data/ and manifests/ prefixes, through
-- the AWS role prod-nyc311-snowflake-read (Terraform, environments/prod). Its
-- trust policy needs STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID from
-- DESC INTEGRATION NYC311_PROD_S3 (terraform.tfvars).
CREATE STORAGE INTEGRATION IF NOT EXISTS NYC311_PROD_S3
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = 'arn:aws:iam::778031418236:role/prod-nyc311-snowflake-read'
  STORAGE_ALLOWED_LOCATIONS = (
    's3://prod-nyc311-observations-778031418236/data/',
    's3://prod-nyc311-observations-778031418236/manifests/'
  );
GRANT USAGE ON INTEGRATION NYC311_PROD_S3 TO ROLE NYC311_PROD_PIPELINE;

-- Prod's own service user (key pair in prod/nyc311/snowflake-service-user
-- and in the GitHub environment "prod"), so the dev key, which CI pull
-- requests use, can never reach prod. It runs migrations, the pipeline and
-- training as the pipeline role; the monitor reads as the dashboard role.
-- Set its public key from the key pair you generate for it:
--   ALTER USER NYC311_PROD_SVC SET RSA_PUBLIC_KEY = '<public key body>';
CREATE USER IF NOT EXISTS NYC311_PROD_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = NYC311_PROD_PIPELINE
  DEFAULT_WAREHOUSE = NYC311_WH
  DEFAULT_SECONDARY_ROLES = ();
GRANT ROLE NYC311_PROD_PIPELINE TO USER NYC311_PROD_SVC;
GRANT ROLE NYC311_PROD_DASHBOARD TO USER NYC311_PROD_SVC;

-- The deployed dashboard's own user: only the dashboard role, so its key
-- (prod/nyc311/snowflake-dashboard-user) can never write or read past the
-- APP views. Set its public key from the key pair you generate for it:
--   ALTER USER NYC311_DASHBOARD SET RSA_PUBLIC_KEY = '<public key body>';
CREATE USER IF NOT EXISTS NYC311_DASHBOARD
  TYPE = SERVICE
  DEFAULT_ROLE = NYC311_PROD_DASHBOARD
  DEFAULT_WAREHOUSE = NYC311_WH
  DEFAULT_SECONDARY_ROLES = ();
GRANT ROLE NYC311_PROD_DASHBOARD TO USER NYC311_DASHBOARD;
