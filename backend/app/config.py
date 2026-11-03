from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    provider_profile: str = os.getenv("PROVIDER_PROFILE", "FREE_ZERO_COST")

    # Gemini (Google AI Studio Free Tier or Paid Tier 1+)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_free_tier_pacing: bool = (
        os.getenv("GEMINI_FREE_TIER_PACING", "true").lower() == "true"
    )

    # Optional Specialist ASR Keys
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    soniox_api_key: str = os.getenv("SONIOX_API_KEY", "")
    sarvam_api_key: str = os.getenv("SARVAM_API_KEY", "")

    # Optional Frontier Translation Keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Modal / GPU Open-Weights Flags
    enable_local_demucs: bool = (
        os.getenv("ENABLE_LOCAL_DEMUCS", "false").lower() == "true"
    )
    enable_local_whisperx: bool = (
        os.getenv("ENABLE_LOCAL_WHISPERX", "false").lower() == "true"
    )
    enable_local_nllb: bool = (
        os.getenv("ENABLE_LOCAL_NLLB", "false").lower() == "true"
    )


def get_settings() -> Settings:
    return Settings()
