from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_every_alembic_revision_fits_the_version_column() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    too_long = [script.revision for script in scripts.walk_revisions() if len(script.revision) > 32]

    assert too_long == []
