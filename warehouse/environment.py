"""Names of one environment's Snowflake and S3 objects."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Environment:
    database: str
    bucket: str
    storage_integration: str
    pipeline_role: str
    dashboard_role: str
    warehouse: str = "NYC311_WH"
    # Live databases auto-ingest from the bucket and run their tasks; test
    # databases do neither.
    live: bool = True
    manifests_stage: str = "@OPS.MANIFESTS_STAGE"

    def for_tests(self, manifests_stage: str) -> "Environment":
        """The test database, reading manifests from a fixture stage."""
        return replace(self, database=self.test_database, live=False, manifests_stage=manifests_stage)

    @property
    def test_database(self) -> str:
        """Owned by the pipeline role; Seam 2 tests rebuild it from scratch."""
        return f"{self.database}_TEST"

    def placeholders(self) -> dict[str, str]:
        """Values for {{...}} in migrations."""
        return {
            "database": self.database,
            "bucket": self.bucket,
            "storage_integration": self.storage_integration,
            "dashboard_role": self.dashboard_role,
            "warehouse": self.warehouse,
            "manifests_stage": self.manifests_stage,
            "auto_ingest": "TRUE" if self.live else "FALSE",
            "task_state": "RESUME" if self.live else "SUSPEND",
        }


DEV = Environment(
    database="NYC311_DEV",
    bucket="dev-nyc311-observations-778031418236",
    storage_integration="NYC311_DEV_S3",
    pipeline_role="NYC311_DEV_PIPELINE",
    dashboard_role="NYC311_DEV_DASHBOARD",
)
