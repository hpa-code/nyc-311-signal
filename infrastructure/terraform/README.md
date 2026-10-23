# Terraform

One folder per environment under `environments/` (`dev`, later `prod`), both in us-east-2, sharing modules under `modules/`.

## Apply dev

```sh
cd infrastructure/terraform/environments/dev
terraform init
terraform plan -out=tfplan   # review before applying
terraform apply tfplan
```

State is local and gitignored. The observation bucket has `prevent_destroy`; removing it needs a deliberate code change.

## Secret values

Terraform creates the Secrets Manager entries but never their values, so state holds no secrets. Put the values after the first apply:

```sh
aws secretsmanager put-secret-value --region us-east-2 \
  --secret-id dev/nyc311/socrata-app-token \
  --secret-string '{"app_token":"..."}'

aws secretsmanager put-secret-value --region us-east-2 \
  --secret-id dev/nyc311/snowflake-service-user \
  --secret-string file://$HOME/nyc311-snowflake-secret.json   # {"account","user","private_key"}; keep outside the repo
```
