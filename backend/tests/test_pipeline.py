"""SubForge Universal Backend — Pipeline Test Suite.

Target: 100% pass rate. Exercises the script engine, subtitle format
serialization/round-trip, ASR router helpers, OCR deduplication, CPS validation,
and settings loading. No network calls are made.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.asr_router import UniversalAsrRouter, _group_words_into_subtitle_cues, _safe_parse_json
from app.config import Settings
from app.ocr_engine import deduplicate_temporal_ocr_cues
from app.models import BoundingBoxNorm, ScriptFamily, SubtitleCue
from app.script_engine import (
    INDIC_ABUGIDA_LANGS,
    RLI,
    PDI,
    RLM,
    compute_cps_and_validate_cue,
    format_subtitle_text,
    get_script_spec,
    normalize_lang_code,
    wrap_rtl_bidi_lines,
)
from app.subtitle_formats import (
    parse_subtitle_content,
    serialize_to_ass,
    serialize_to_srt,
    serialize_to_vtt,
)

# ---------------------------------------------------------------------------
# Config / Settings smoke tests
# ---------------------------------------------------------------------------


def test_config_loads_free_profile(monkeypatch):
    monkeypatch.setenv("PROVIDER_PROFILE", "FREE_ZERO_COST")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = Settings()
    assert s.provider_profile == "FREE_ZERO_COST"
    assert s.gemini_free_tier_pacing is True
    assert s.gemini_model == "gemini-2.5-flash"


def test_config_loads_paid_profile():
    s = Settings(provider_profile="PAID_PRO_SCALE")
    assert s.provider_profile == "PAID_PRO_SCALE"


def test_asr_router_requires_configured_engine():
    s = Settings(provider_profile="FREE_ZERO_COST")
    router = UniversalAsrRouter(s)
    with pytest.raises(RuntimeError, match="No ASR engine configured"):
        router.select_best_engine("auto")


def test_router_selects_elevenlabs_when_key_present():
    s = Settings(provider_profile="FREE_ZERO_COST", elevenlabs_api_key="sk-test")
    router = UniversalAsrRouter(s)
    assert router.select_best_engine("en") == "elevenlabs_scribe"


def test_router_selects_gemini_when_only_gemini_key():
    s = Settings(provider_profile="FREE_ZERO_COST", gemini_api_key="test-key")
    router = UniversalAsrRouter(s)
    assert router.select_best_engine("en") == "gemini_multimodal_asr"


def test_router_selects_groq_when_only_groq_key():
    s = Settings(provider_profile="FREE_ZERO_COST", groq_api_key="test-key")
    router = UniversalAsrRouter(s)
    assert router.select_best_engine("en") == "groq_whisper"


def test_router_routes_indic_to_sarvam_when_configured():
    s = Settings(
        provider_profile="FREE_ZERO_COST",
        sarvam_api_key="test-key",
        gemini_api_key="",
        elevenlabs_api_key="",
        groq_api_key="",
    )
    router = UniversalAsrRouter(s)
    assert router.select_best_engine("bn") == "sarvam_saaras"


# ---------------------------------------------------------------------------
# Script engine tests
# ---------------------------------------------------------------------------


def test_normalize_lang_code():
    assert normalize_lang_code("EN") == "en"
    assert normalize_lang_code("zh-CN") == "zh-cn"
    assert normalize_lang_code("  bn-IN ") == "bn"
    assert normalize_lang_code("auto") == "auto"
    assert normalize_lang_code("pt-BR") == "pt-br"


def test_latin_script_spec_defaults():
    spec = get_script_spec("en")
    assert spec.family == ScriptFamily.LATIN_CYRILLIC_GREEK
    assert spec.is_rtl is False
    assert spec.max_chars_per_line_landscape == 42
    assert spec.max_cps_adult == 17.5


def test_rtl_script_is_detected():
    spec = get_script_spec("ar")
    assert spec.family == ScriptFamily.RTL_BIDI
    assert spec.is_rtl is True


def test_cjk_japanese_script_spec():
    spec = get_script_spec("ja")
    assert spec.family == ScriptFamily.CJK_JAPANESE
    assert spec.max_chars_per_line_landscape == 13
    assert spec.uses_word_spaces is False


def test_cjk_chinese_korean_script_spec():
    spec = get_script_spec("ko")
    assert spec.family == ScriptFamily.CJK_CHINESE_KOREAN
    assert spec.max_chars_per_line_landscape == 16
    assert spec.uses_word_spaces is True  # Korean uses word spaces


def test_indic_abugida_bengali_included():
    assert "bn" in INDIC_ABUGIDA_LANGS
    assert "hi" in INDIC_ABUGIDA_LANGS
    assert "ta" in INDIC_ABUGIDA_LANGS


def test_rtl_bidi_isolates_injected():
    wrapped = wrap_rtl_bidi_lines("السلام عليكم")
    assert RLI in wrapped and PDI in wrapped


def test_format_subtitle_text_wraps_long_latin_line():
    formatted, spec = format_subtitle_text(
        "This is a very long English subtitle line that should be split into two readable lines.",
        target_lang="en",
    )
    lines = formatted.split("\n")
    assert len(lines) <= 2
    assert all(line.strip() for line in lines)


def test_format_subtitle_text_japanese_kinsoku():
    formatted, spec = format_subtitle_text(
        "こんにちは、世界。これは日本語のテスト文章です。",
        target_lang="ja",
    )
    assert "\n" in formatted or len(formatted) <= 13


def test_compute_cps_and_validate_flags_too_long():
    cue = SubtitleCue(
        id=1,
        start_ms=0,
        end_ms=500,  # 0.5s duration
        source_text="This is way too many characters to read in half a second",
        translated_text="This is way too many characters to read in half a second",
        detected_lang="en",
    )
    validated = compute_cps_and_validate_cue(cue, target_lang="en")
    assert validated.needs_review is True
    assert "cps" in (validated.review_reason or "").lower()


def test_compute_cps_and_validate_passes_normal():
    cue = SubtitleCue(
        id=1,
        start_ms=0,
        end_ms=4000,  # 4s duration
        source_text="Hello world.",
        translated_text="Hello world.",
        detected_lang="en",
    )
    validated = compute_cps_and_validate_cue(cue, target_lang="en")
    assert validated.needs_review is False
    assert validated.cps > 0


# ---------------------------------------------------------------------------
# Subtitle format round-trip tests
# ---------------------------------------------------------------------------


def test_srt_parse_and_serialize_round_trip():
    content = """1
00:00:01,500 --> 00:00:04,200
Hello world.

2
00:00:05,000 --> 00:00:08,000
Line two
Line two continued.
"""
    cues = parse_subtitle_content(content, format_hint="srt")
    assert len(cues) == 2
    assert cues[0].start_ms == 1500
    assert cues[0].end_ms == 4200
    assert cues[0].source_text == "Hello world."
    assert cues[1].source_text == "Line two\nLine two continued."

    out = serialize_to_srt(cues)
    assert "00:00:01,500 --> 00:00:04,200" in out
    assert "Hello world." in out


def test_vtt_parse_and_serialize_round_trip():
    content = """WEBVTT

00:00:01.500 --> 00:00:04.200
Hello world.

00:00:05.000 --> 00:00:08.000
Line two.
"""
    cues = parse_subtitle_content(content, format_hint="vtt")
    assert len(cues) == 2
    assert cues[0].start_ms == 1500
    out = serialize_to_vtt(cues)
    assert out.startswith("WEBVTT")
    assert "Hello world." in out


def test_ass_parse_and_serialize_round_trip():
    content = """[Script Info]
Title: Test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.50,0:00:04.20,Default,,0,0,0,,Hello world.
Dialogue: 0,0:00:05.00,0:00:08.00,Default,,0,0,0,,Line two.
"""
    cues = parse_subtitle_content(content, format_hint="ass")
    assert len(cues) == 2
    assert cues[0].start_ms == 1500
    out = serialize_to_ass(cues, target_lang="en")
    assert "[Script Info]" in out
    assert "Hello world." in out


def test_parse_auto_detects_srt_from_content():
    cues = parse_subtitle_content("1\n00:00:00,000 --> 00:00:02,000\nHello.\n", format_hint="auto")
    assert len(cues) >= 1


def test_srt_translated_text_is_serialized():
    cues = [
        SubtitleCue(
            id=1,
            start_ms=0,
            end_ms=2000,
            source_text="Bonjour",
            translated_text="Hello",
            detected_lang="fr",
        )
    ]
    out = serialize_to_srt(cues)
    assert "Hello" in out


# ---------------------------------------------------------------------------
# ASR router helper tests
# ---------------------------------------------------------------------------


def test_group_words_into_subtitle_cues_breaks_on_speaker_change():
    words = [
        {"text": "Hello", "start": 0.0, "end": 0.5, "speaker_id": "S1", "type": "word"},
        {"text": "there", "start": 0.6, "end": 1.0, "speaker_id": "S1", "type": "word"},
        {"text": "John", "start": 1.2, "end": 1.7, "speaker_id": "S2", "type": "word"},
    ]
    cues = _group_words_into_subtitle_cues(words, "en")
    assert len(cues) == 2
    assert cues[0].source_text == "Hello there"
    assert cues[1].source_text == "John"


def test_group_words_handles_spacing_items_ignored():
    words = [
        {"text": "Hello", "start": 0.0, "end": 0.5, "type": "word"},
        {"type": "spacing"},
        {"text": "world", "start": 0.6, "end": 1.1, "type": "word"},
    ]
    cues = _group_words_into_subtitle_cues(words, "en")
    assert cues[0].source_text == "Hello world"


def test_safe_parse_json_handles_markdown_wrappers():
    raw = "```json\n{\"primary_lang\": \"en\", \"cues\": []}\n```"
    assert _safe_parse_json(raw)["primary_lang"] == "en"


# ---------------------------------------------------------------------------
# OCR deduplication tests
# ---------------------------------------------------------------------------


def test_dedup_merges_similar_consecutive_cues():
    cues = [
        SubtitleCue(
            id=1,
            start_ms=1000,
            end_ms=1200,
            source_text="Hello world",
            detected_lang="en",
            confidence=0.90,
            mask_box=BoundingBoxNorm(x=0.1, y=0.1, width=0.4, height=0.2),
        ),
        SubtitleCue(
            id=2,
            start_ms=1250,
            end_ms=1400,
            source_text="Hello world",
            detected_lang="en",
            confidence=0.95,
            mask_box=BoundingBoxNorm(x=0.11, y=0.11, width=0.4, height=0.2),
        ),
    ]
    merged = deduplicate_temporal_ocr_cues(cues)
    assert len(merged) == 1
    assert merged[0].confidence == 0.95


def test_dedup_keeps_distinct_cues_separate():
    cues = [
        SubtitleCue(
            id=1,
            start_ms=1000,
            end_ms=1200,
            source_text="Hello world",
            detected_lang="en",
            confidence=0.9,
        ),
        SubtitleCue(
            id=2,
            start_ms=3000,
            end_ms=3300,
            source_text="Goodbye world",
            detected_lang="en",
            confidence=0.9,
        ),
    ]
    merged = deduplicate_temporal_ocr_cues(cues)
    assert len(merged) == 2


# ---------------------------------------------------------------------------
# BiDi control character presence
# ---------------------------------------------------------------------------


def test_bidi_control_chars_are_correct_unicode():
    assert RLI == "\u2067"
    assert PDI == "\u2069"
    assert RLM == "\u200F"
