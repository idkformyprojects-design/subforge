from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class ScriptFamily(str, Enum):
    LATIN_CYRILLIC_GREEK = "LATIN_CYRILLIC_GREEK"
    CJK_JAPANESE = "CJK_JAPANESE"
    CJK_CHINESE_KOREAN = "CJK_CHINESE_KOREAN"
    RTL_BIDI = "RTL_BIDI"
    SEA_UNSPACED = "SEA_UNSPACED"
    INDIC_ABUGIDA = "INDIC_ABUGIDA"
    GLOBAL_OTHER = "GLOBAL_OTHER"


class TranslationStyle(str, Enum):
    CINEMA_NATURAL = "CINEMA_NATURAL"
    STRICT_LITERAL = "STRICT_LITERAL"
    BILINGUAL_DUAL = "BILINGUAL_DUAL"


class BoundingBoxNorm(BaseModel):
    """Normalized [0.0..1.0] video coordinates of original burned-in subtitle for dynamic pill masking."""
    x: float = Field(0.1, ge=0.0, le=1.0)
    y: float = Field(0.82, ge=0.0, le=1.0)
    width: float = Field(0.8, ge=0.0, le=1.0)
    height: float = Field(0.12, ge=0.0, le=1.0)


class WordTiming(BaseModel):
    word: str
    start_ms: int
    end_ms: int
    confidence: float = 0.95


class SubtitleCue(BaseModel):
    id: int
    start_ms: int
    end_ms: int
    source_text: str = ""
    translated_text: str = ""
    detected_lang: str = "auto"
    speaker: Optional[str] = None
    is_song: bool = False
    confidence: float = 0.95
    needs_review: bool = False
    review_reason: Optional[str] = None
    cps: float = 0.0
    mask_box: Optional[BoundingBoxNorm] = None
    words: List[WordTiming] = Field(default_factory=list)


class CharacterProfile(BaseModel):
    name: str
    inferred_gender: str = "unknown"
    role_or_relationship: str = ""
    formality_register: str = "neutral"


class StoryBible(BaseModel):
    movie_summary: str = ""
    genre_and_tone: str = "drama"
    primary_source_languages: List[str] = Field(default_factory=list)
    characters: List[CharacterProfile] = Field(default_factory=list)
    glossary: Dict[str, str] = Field(default_factory=dict)


class ScriptSpec(BaseModel):
    family: ScriptFamily
    max_chars_per_line_landscape: int
    max_chars_per_line_portrait: int
    max_cps_adult: float
    is_rtl: bool = False
    uses_word_spaces: bool = True
    line_height_multiplier: float = 1.2


class AudioChunkDescriptor(BaseModel):
    chunk_index: int
    offset_ms: int
    duration_ms: int
    filename: str


class SoftSubtitleTranslateRequest(BaseModel):
    content: str = Field(..., description="Raw SRT, VTT, or ASS content extracted from video")
    format_hint: str = Field("srt", description="srt | vtt | ass")
    source_lang: str = Field("auto", description="ISO language code or 'auto'")
    target_lang: str = Field(..., description="Target ISO language code, e.g., 'en', 'es', 'ja', 'ar', 'bn'")
    style: TranslationStyle = TranslationStyle.CINEMA_NATURAL
    is_portrait_video: bool = False
    foreign_dialogue_only_lang: Optional[str] = Field(
        None,
        description="If set (e.g. 'en'), cues spoken in this language remain unsubtitled (Forced Narrative mode)",
    )
    gemini_api_key_override: Optional[str] = None


class HardSubStripMeta(BaseModel):
    strip_id: int
    start_ms: int
    end_ms: int
    mask_box: BoundingBoxNorm


class ContactSheetManifest(BaseModel):
    sheet_index: int
    strips: List[HardSubStripMeta]


class RetranslateCueRequest(BaseModel):
    cue: SubtitleCue
    target_lang: str
    context_prev: List[str] = Field(default_factory=list)
    context_next: List[str] = Field(default_factory=list)
    user_instruction: str = ""
    story_bible: Optional[StoryBible] = None
    is_portrait_video: bool = False
    gemini_api_key_override: Optional[str] = None


class SubtitleProjectResponse(BaseModel):
    source_lang_detected: str
    target_lang: str
    script_spec: ScriptSpec
    story_bible: StoryBible
    cues: List[SubtitleCue]
    uncertain_cue_ids: List[int]
    srt_output: str
    vtt_output: str
    ass_output: str
    engine_summary: Dict[str, str] = Field(default_factory=dict)
