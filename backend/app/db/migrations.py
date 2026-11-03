"""CWD-independent Alembic configuration helper."""

from pathlib import Path
from typing import Final

from alembic.config import Config

from app.core.config import BACKEND_DIR

ALEMBIC_INI_PATH: Final[Path] = BACKEND_DIR / "alembic.ini"
MIGRATIONS_DIR: Final[Path] = BACKEND_DIR / "migrations"


def build_alembic_config(
    database_url: str | None = None,
    *,
    configure_logger: bool = False,
) -> Config:
    """Create an Alembic Config bound to `backend/alembic.ini` regardless of CWD."""
    alembic_cfg = Config(str(ALEMBIC_INI_PATH))
    alembic_cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    alembic_cfg.attributes["configure_logger"] = configure_logger
    if database_url is not None:
        # ConfigParser treats `%` as interpolation; escape URL-encoded chars (`%3A`, etc.).
        alembic_cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return alembic_cfg
