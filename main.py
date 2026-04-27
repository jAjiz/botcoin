from core.validation import validate_config
from api.app import app  # noqa: F401  (uvicorn target: `uvicorn main:app`)

if not validate_config():
    raise SystemExit(1)
