"""
Centralised settings loaded from .env via pydantic-settings.
All other modules import `settings` from here — no os.environ calls elsewhere.
"""
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Product keywords
    search_products: str = "Rose Water"

    # Crawl limits
    max_pages: int = 2
    request_delay: float = 3.0
    request_timeout: int = 15
    max_retries: int = 3

    # Output
    output_file: str = "output/rose_water_customers.xlsx"
    overwrite_output: bool = True

    # Storage
    db_path: str = "data/leads.db"
    raw_html_dir: str = "data/raw_html"

    # Rendering
    use_playwright: bool = False

    # Logging
    log_level: str = "INFO"
    log_dir: str = "logs"

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @property
    def products(self) -> List[str]:
        """Return search_products as a list (strips whitespace)."""
        return [p.strip() for p in self.search_products.split(",") if p.strip()]

    @property
    def db_path_abs(self) -> Path:
        return Path(self.db_path)

    @property
    def raw_html_dir_abs(self) -> Path:
        p = Path(self.raw_html_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_file_abs(self) -> Path:
        p = Path(self.output_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir_abs(self) -> Path:
        p = Path(self.log_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


# Module-level singleton — import this everywhere
settings = Settings()
