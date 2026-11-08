import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Tests must never touch real services or the developer's local DB.
os.environ["GROQ_API_KEY"] = ""
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_KEY"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["SIMULATOR_ENABLED"] = "0"
os.environ["SQLITE_PATH"] = ":memory:"

from app.config import settings  # noqa: E402

settings.reload()

# Wednesday 4 Nov 2026, 19:00 IST (evening)
WED_EVENING = datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app import db as dbmod
    from scripts.seed import seed

    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    database = dbmod.SQLiteDatabase(":memory:")
    dbmod.set_db(database)
    seed(database, now=WED_EVENING, quiet=True)
    yield database
    dbmod.set_db(None)


@pytest.fixture()
def now():
    return WED_EVENING
