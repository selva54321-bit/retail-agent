from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ApiSettings:
    app_name: str = "RetailAgent API"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    host: str = os.environ.get("API_HOST", "0.0.0.0")
    port: int = int(os.environ.get("API_PORT", "8000"))
    cors_origins: list[str] = None

    def __post_init__(self):
        origins = os.environ.get("API_CORS_ORIGINS", "*")
        parsed = [o.strip() for o in origins.split(",") if o.strip()]
        object.__setattr__(self, "cors_origins", parsed or ["*"])


settings = ApiSettings()
