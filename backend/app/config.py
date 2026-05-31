import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent


def find_repo_root() -> Path:
    """Resolve monorepo root whether cwd is repo root or backend/ (e.g. Railway)."""
    if (_REPO_ROOT / "mcp_server" / "server.py").exists():
        return _REPO_ROOT
    if (_BACKEND_DIR / "mcp_server" / "server.py").exists():
        return _BACKEND_DIR
    env_root = os.getenv("QUANTPILOT_ROOT")
    if env_root:
        return Path(env_root)
    return _REPO_ROOT


REPO_ROOT = find_repo_root()
BACKEND_DIR = REPO_ROOT / "backend" if (REPO_ROOT / "backend").is_dir() else _BACKEND_DIR

_env_file = BACKEND_DIR / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv()


def get_allowed_origins() -> List[str]:
    raw = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
