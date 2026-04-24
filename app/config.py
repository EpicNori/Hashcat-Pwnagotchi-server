import secrets
import os
from pathlib import Path


def _default_cache_dir() -> Path:
    try:
        return Path.home() / ".hashcat" / "wpa-server"
    except RuntimeError:
        fallback_base = (
            os.environ.get("USERPROFILE")
            or os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or r"C:\Users\Public"
        )
        return Path(fallback_base) / ".hashcat" / "wpa-server"


HASHCAT_WPA_CACHE_DIR = Path(
    os.environ.get("HASHCAT_WPA_SERVER_HOME", _default_cache_dir())
)
ROOT_PRIVATE_DIR = Path(__file__).parent.parent

WORDLISTS_DIR = ROOT_PRIVATE_DIR / "wordlists"
WORDLISTS_USER_DIR = HASHCAT_WPA_CACHE_DIR / "wordlists"  # user custom wordlists
RULES_DIR = ROOT_PRIVATE_DIR / "rules"
MASKS_DIR = ROOT_PRIVATE_DIR / "masks"
LOGS_DIR = ROOT_PRIVATE_DIR / "logs"

DATABASE_DIR = HASHCAT_WPA_CACHE_DIR / "database"
ESSID_TRIED = DATABASE_DIR / "essid_tried"
DATABASE_PATH = DATABASE_DIR / "hashcat_wpa.db"

# Hashcat
# Keep status updates responsive enough for throttling and UI progress.
HASHCAT_STATUS_TIMER = 5  # seconds
BENCHMARK_FILE = HASHCAT_WPA_CACHE_DIR / "benchmark.csv"
HASHCAT_BRAIN_PASSWORD_PATH = HASHCAT_WPA_CACHE_DIR / "brain" / "hashcat_brain_password"
ADMIN_SETTINGS_PATH = HASHCAT_WPA_CACHE_DIR / "admin_settings.json"
APP_UPDATE_PROGRESS_FILE = "app_update.progress"
NVIDIA_INSTALL_PROGRESS_FILE = "nvidia_install.progress"

# mkdirs
HASHCAT_WPA_CACHE_DIR.mkdir(exist_ok=True, parents=True)
WORDLISTS_USER_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
DATABASE_DIR.mkdir(exist_ok=True)
HASHCAT_BRAIN_PASSWORD_PATH.parent.mkdir(exist_ok=True)

class Config:
    """ Flask application config """

    # Persistent Secret Key
    _secret_file = HASHCAT_WPA_CACHE_DIR / ".secret_key"
    if _secret_file.exists():
        SECRET_KEY = _secret_file.read_bytes()
    else:
        SECRET_KEY = secrets.token_bytes(64)
        _secret_file.write_bytes(SECRET_KEY)

    REMEMBER_COOKIE_DURATION = 3600 * 24 * 30  # 30 days
    PERMANENT_SESSION_LIFETIME = 3600 * 24 * 30  # 30 days

    # Flask-SQLAlchemy settings
    SQLALCHEMY_DATABASE_URI = "sqlite:///{}".format(DATABASE_PATH)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Airodump capture files
    CAPTURES_DIR = HASHCAT_WPA_CACHE_DIR / "captures"
