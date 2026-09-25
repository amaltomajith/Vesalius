"""Central settings. Values reserved for later stages (S4's de-id secrets) are declared
here now so the env/config surface doesn't need rework later, per CLAUDE.md rule 9 and
DICOM.md's `pseudo_uid` / `patient_offset_days`. No S1/S2 code path reads them.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    pipeline_version: str = "0.1.0"

    # Database -- two roles, two connection strings. serving_ro is unused until S12.
    pipeline_database_url: str = (
        "postgresql+psycopg://pipeline_svc:pipeline_svc@localhost:5432/pipeline"
    )
    serving_database_url: str = "postgresql+psycopg://serving_ro:serving_ro@localhost:5432/pipeline"

    # MinIO -- one bucket, one credential pair, per zone.
    minio_endpoint: str = "localhost:9000"
    minio_secure: bool = False
    # Host that presigned URLs are signed for. The host is part of the signature, and inside
    # compose the internal `minio:9000` is unreachable from a client, so S12 signs for this one.
    # Empty = same as `minio_endpoint`.
    minio_public_endpoint: str = ""
    presign_expiry_seconds: int = 300

    minio_bronze_bucket: str = "bronze"
    minio_bronze_access_key: str = "bronze-access"
    minio_bronze_secret_key: str = "bronze-secret"

    minio_silver_bucket: str = "silver"
    minio_silver_access_key: str = "silver-access"
    minio_silver_secret_key: str = "silver-secret"

    minio_gold_bucket: str = "gold"
    minio_gold_access_key: str = "gold-access"
    minio_gold_secret_key: str = "gold-secret"

    minio_quarantine_bucket: str = "quarantine"
    minio_quarantine_access_key: str = "quarantine-access"
    minio_quarantine_secret_key: str = "quarantine-secret"

    # Reserved for S4. Declared now for forward-compatible env/config; unread by S1/S2.
    deid_uid_secret: str = ""
    deid_date_shift_secret: str = ""


def get_settings() -> Settings:
    return Settings()
