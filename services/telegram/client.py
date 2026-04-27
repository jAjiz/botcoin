import os

import httpx

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

client = httpx.AsyncClient(base_url=API_BASE_URL, timeout=5.0)
