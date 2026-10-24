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

    def with_database(self, database: str) -> "Environment":
        return replace(self, database=database)

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
        }


DEV = Environment(
    database="NYC311_DEV",
    bucket="dev-nyc311-observations-778031418236",
    storage_integration="NYC311_DEV_S3",
    pipeline_role="NYC311_DEV_PIPELINE",
    dashboard_role="NYC311_DEV_DASHBOARD",
)
