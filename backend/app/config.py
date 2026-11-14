"""Runtime settings, all from environment variables (Replit Secrets)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_DIR = DATA_DIR / "queue"
QUEUE_DIR.mkdir(exist_ok=True)


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{DATA_DIR / 'app.db'}"
    if url.startswith("postgres://"):  # SQLAlchemy needs the long form
        url = "postgresql://" + url[len("postgres://"):]
    return url


DATABASE_URL = _db_url()
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED") == "1"  # tests / local only

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

# NetSuite SuiteQL (OAuth 2.0 machine-to-machine). All optional: without them
# the app runs on uploaded report exports only.
NS_ACCOUNT_ID = os.environ.get("NS_ACCOUNT_ID", "")
NS_CLIENT_ID = os.environ.get("NS_CLIENT_ID", "")
NS_CERT_ID = os.environ.get("NS_CERT_ID", "")
NS_PRIVATE_KEY = os.environ.get("NS_PRIVATE_KEY", "")  # PEM text
_field = os.environ.get("NS_SHIPMENT_ID_FIELD", "")  # e.g. custrecord_xx_shipment_id
NS_SHIPMENT_ID_FIELD = _field if _field.replace("_", "").isalnum() else ""  # goes into SQL: identifiers only

MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_ZIP_MEMBERS = 3000
PARSE_PAGES = 4          # charge tables sit on the first pages; the rest are scans
TIE_TOLERANCE = 0.02
KEEP_SNAPSHOTS = 5
