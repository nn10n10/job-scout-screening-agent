"""Execute the existing Python daily entry point with the WebUI's database root."""
from pathlib import Path
import sys

from scout_agent.cli import main
from scout_agent.config import load_settings


if __name__ == '__main__':
    db_path = Path(sys.argv[1]).resolve()
    settings = load_settings(db_path.parent.parent)
    if settings.db_path.resolve() != db_path:
        raise SystemExit(2)
    raise SystemExit(main(['daily'], _settings=settings))
