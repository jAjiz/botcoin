import os

import httpx

from core.config import API_SECRET_TOKEN

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")

client = httpx.AsyncClient(
    base_url=API_BASE_URL,
    timeout=5.0,
    headers={"X-Api-Token": API_SECRET_TOKEN} if API_SECRET_TOKEN else {},
)
