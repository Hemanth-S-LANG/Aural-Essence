"""
Centralised settings loaded from .env via pydantic-settings.
All other modules import `settings` from here — no os.environ calls elsewhere.
"""
from pathlib import Path
from typing import List, Optional

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

    @field_validator("request_delay")
    @classmethod
    def _min_request_delay(cls, v: float) -> float:
        """Enforce a minimum 3-second delay between requests (ethical constraint)."""
        if v < 3.0:
            raise ValueError(
                f"request_delay must be >= 3.0 seconds (got {v}). "
                "Lower values risk overloading target servers."
            )
        return v

    # Output
    output_file: str = "output/rose_water_customers.xlsx"
    output_file_tradeindia: str = "output/rose_water_tradeindia.xlsx"
    output_file_meesho: str = "output/rose_water_meesho.xlsx"
    output_file_all: str = "output/rose_water_all.xlsx"
    # OneDrive / network folder (optional). When set, exports are ALSO written here.
    # Accepts absolute Windows paths, e.g.: C:\Users\Dell\OneDrive\AuralEssence\leads.xlsx
    output_file_onedrive: str = ""
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

    def output_path_for_source(self, source: str) -> Path:
        """Return the configured output path for a given source label."""
        mapping = {
            "tradeindia": self.output_file_tradeindia,
            "meesho":     self.output_file_meesho,
            "all":        self.output_file_all,
        }
        raw = mapping.get(source.lower(), self.output_file)
        p = Path(raw)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def onedrive_path(self) -> Optional[Path]:
        """Return the OneDrive output path if configured, else None."""
        if not self.output_file_onedrive:
            return None
        p = Path(self.output_file_onedrive)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass   # OneDrive folder may not exist on this machine
        return p

    @property
    def log_dir_abs(self) -> Path:
        p = Path(self.log_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


# Module-level singleton — import this everywhere
settings = Settings()
