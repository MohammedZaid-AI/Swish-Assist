"""Environment loading. Everything configurable lives here."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
        self.GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
        self.GROQ_CHAT_FALLBACK_MODEL = os.getenv("GROQ_CHAT_FALLBACK_MODEL", "openai/gpt-oss-20b")
        self.GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
        self.GROQ_VISION_FALLBACK_MODEL = os.getenv(
            "GROQ_VISION_FALLBACK_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
        )
        self.SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
        self.SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
        self.TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
        self.TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
        self.TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip()
        self.AUTO_REFUND_MAX_INR = _float("AUTO_REFUND_MAX_INR", 300)
        self.AUTO_REFUND_MIN_CONFIDENCE = _float("AUTO_REFUND_MIN_CONFIDENCE", 0.8)
        self.PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        self.SQLITE_PATH = os.getenv("SQLITE_PATH", str(ROOT / "data" / "swish.db"))
        self.UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(ROOT / "data" / "uploads")))
        self.SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "1") == "1"
        self.GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/")
        # Keep-alive: ping our own /health so Render's free plan never puts the app to sleep.
        # On Render (RENDER is set there) it defaults to every 10 minutes; elsewhere it is off. 0 turns it off.
        self.KEEP_ALIVE_MINUTES = _float("KEEP_ALIVE_MINUTES", 10 if os.getenv("RENDER") else 0)
        self.KEEP_ALIVE_URL = (os.getenv("KEEP_ALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL")
                               or os.getenv("PUBLIC_BASE_URL", "")).strip().rstrip("/")

    @property
    def use_supabase(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_KEY)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.GROQ_API_KEY)


settings = Settings()
