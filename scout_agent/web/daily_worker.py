"""Execute the existing Python daily entry point with the WebUI's database root."""
import json
from pathlib import Path
import sys

from scout_agent.cli import main
from scout_agent.config import load_settings
from scout_agent.daily import normalize_platforms


if __name__ == '__main__':
    db_path = Path(sys.argv[1]).resolve()
    settings = load_settings(db_path.parent.parent)
    if settings.db_path.resolve() != db_path:
        raise SystemExit(2)
    raise SystemExit(main(['daily'], _settings=settings,
        _daily_platforms=normalize_platforms(sys.argv[2:]),
        _daily_progress=lambda event: print("Daily event: " + json.dumps(event), flush=True)))
