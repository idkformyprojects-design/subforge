from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .asr_router import UniversalAsrRouter
from .config import get_settings
from .models import (
    AudioChunkDescriptor,
    ContactSheetManifest,
    RetranslateCueRequest,
    SoftSubtitleTranslateRequest,
    SubtitleCue,
    SubtitleProjectResponse,
    TranslationStyle,
)
from .ocr_engine import UniversalHardSubOcrEngine
from .script_engine import get_script_spec
from .story_bible_translator import UniversalStoryBibleTranslator
from .subtitle_formats import (
    parse_subtitle_content,
    serialize_to_ass,
    serialize_to_srt,
    serialize_to_vtt,
)

app = FastAPI(
    title="SubForge Universal AI Subtitle Engine",
    version="1.0.0",
    description=(
        "Split-Plane Cloud AI Orchestrator for Any-Language Spoken Audio -> Subtitles "
        "and Soft/Hardcoded Subtitle Translation across 200+ World Languages."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GLOBAL_LANGUAGES: List[Dict[str, str]] = [
    {"code": "auto", "name": "Auto-Detect Spoken/Written Language (Multi-Language Ready)"},
    {"code": "en", "name": "English"},
    {"code": "es", "name": "Spanish (Español)"},
    {"code": "fr", "name": "French (Français)"},
    {"code": "de", "name": "German (Deutsch)"},
    {"code": "pt", "name": "Portuguese (Português)"},
    {"code": "it", "name": "Italian (Italiano)"},
    {"code": "ru", "name": "Russian (Русский)"},
    {"code": "ja", "name": "Japanese (日本語)"},
    {"code": "ko", "name": "Korean (한국어)"},
    {"code": "zh-cn", "name": "Chinese Simplified (简体中文)"},
    {"code": "zh-tw", "name": "Chinese Traditional (繁體中文)"},
    {"code": "ar", "name": "Arabic (العربية)"},
    {"code": "hi", "name": "Hindi (हिन्दी)"},
    {"code": "bn", "name": "Bengali (বাংলা)"},
    {"code": "ur", "name": "Urdu (اردو)"},
    {"code": "fa", "name": "Persian / Farsi (فارسی)"},
    {"code": "he", "name": "Hebrew (עברית)"},
    {"code": "tr", "name": "Turkish (Türkçe)"},
    {"code": "vi", "name": "Vietnamese (Tiếng Việt)"},
    {"code": "th", "name": "Thai (ไทย)"},
    {"code": "id", "name": "Indonesian (Bahasa Indonesia)"},
    {"code": "ms", "name": "Malay (Bahasa Melayu)"},
    {"code": "tl", "name": "Filipino / Tagalog"},
    {"code": "ta", "name": "Tamil (தமிழ்)"},
    {"code": "te", "name": "Telugu (తెలుగు)"},
    {"code": "ml", "name": "Malayalam (മലയാളം)"},
    {"code": "mr", "name": "Marathi (मराठी)"},
    {"code": "gu", "name": "Gujarati (ગુજરાતી)"},
    {"code": "kn", "name": "Kannada (ಕನ್ನಡ)"},
    {"code": "pa", "name": "Punjabi (ਪੰਜਾਬੀ)"},
    {"code": "ne", "name": "Nepali (नेपाली)"},
    {"code": "si", "name": "Sinhala (සිංහල)"},
    {"code": "my", "name": "Burmese (မြန်မာ)"},
    {"code": "km", "name": "Khmer (ខ្មែរ)"},
    {"code": "lo", "name": "Lao (ລາວ)"},
    {"code": "uk", "name": "Ukrainian (Українська)"},
    {"code": "pl", "name": "Polish (Polski)"},
    {"code": "nl", "name": "Dutch (Nederlands)"},
    {"code": "sv", "name": "Swedish (Svenska)"},
    {"code": "no", "name": "Norwegian (Norsk)"},
    {"code": "da", "name": "Danish (Dansk)"},
    {"code": "fi", "name": "Finnish (Suomi)"},
    {"code": "el", "name": "Greek (Ελληνικά)"},
    {"code": "cs", "name": "Czech (Čeština)"},
    {"code": "ro", "name": "Romanian (Română)"},
    {"code": "hu", "name": "Hungarian (Magyar)"},
    {"code": "sr", "name": "Serbian (Српски)"},
    {"code": "hr", "name": "Croatian (Hrvatski)"},
    {"code": "bg", "name": "Bulgarian (Български)"},
    {"code": "sw", "name": "Swahili (Kiswahili)"},
    {"code": "am", "name": "Amharic (አማርኛ)"},
    {"code": "ha", "name": "Hausa"},
    {"code": "yo", "name": "Yoruba"},
    {"code": "zu", "name": "Zulu"},
    {"code": "ka", "name": "Georgian (ქართული)"},
    {"code": "hy", "name": "Armenian (Հայերեն)"},
    {"code": "az", "name": "Azerbaijani"},
    {"code": "kk", "name": "Kazakh (Қазақша)"},
    {"code": "uz", "name": "Uzbek"},
]


def _build_project_response(
    cues: List[SubtitleCue],
    source_detected: str,
    target_lang: str,
    story_bible,
    engines: Dict[str, str],
    opaque_pill_box: bool = False,
) -> SubtitleProjectResponse:
    spec = get_script_spec(target_lang)
    uncertain_ids = [c.id for c in cues if c.needs_review]
    return SubtitleProjectResponse(
        source_lang_detected=source_detected,
        target_lang=target_lang,
        script_spec=spec,
        story_bible=story_bible,
        cues=cues,
        uncertain_cue_ids=uncertain_ids,
        srt_output=serialize_to_srt(cues),
        vtt_output=serialize_to_vtt(cues),
        ass_output=serialize_to_ass(cues, target_lang=target_lang, opaque_pill_box=opaque_pill_box),
        engine_summary=engines,
    )


@app.get("/health")
async def health_check() -> Dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "SubForge Universal AI Engine",
        "profile": settings.provider_profile,
        "gemini_configured": str(bool(settings.gemini_api_key)),
    }


@app.get("/v1/languages")
async def list_supported_languages() -> Dict[str, object]:
    enriched = []
    for item in GLOBAL_LANGUAGES:
        code = item["code"]
        spec = get_script_spec("en" if code == "auto" else code)
        enriched.append({**item, "script_family": spec.family.value, "is_rtl": spec.is_rtl})
    return {"count": len(enriched), "languages": enriched}


@app.post("/v1/translate-soft-subtitles", response_model=SubtitleProjectResponse)
async def translate_soft_subtitles(req: SoftSubtitleTranslateRequest) -> SubtitleProjectResponse:
    """
    Feature 2A/2B: Accepts embedded SRT / VTT / ASS text extracted losslessly on-device (<1s),
    builds a Whole-Movie Story Bible, translates directly Source -> Target across any of 200+
    languages, enforces script-specific CPS/CPL/BiDi rules, and returns SRT + VTT + ASS + flags.
    """
    settings = get_settings()
    cues = parse_subtitle_content(req.content, format_hint=req.format_hint)
    if not cues:
        raise HTTPException(status_code=400, detail="No valid subtitle cues found in input content.")

    translator = UniversalStoryBibleTranslator(settings)
    try:
        translated_cues, story_bible, trans_engine = await translator.translate_movie_cues(
            cues=cues,
            source_lang=req.source_lang,
            target_lang=req.target_lang,
            style=req.style,
            is_portrait=req.is_portrait_video,
            foreign_dialogue_only_lang=req.foreign_dialogue_only_lang,
            gemini_key_override=req.gemini_api_key_override,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detected = (
        story_bible.primary_source_languages[0]
        if story_bible.primary_source_languages
        else req.source_lang
    )
    return _build_project_response(
        cues=translated_cues,
        source_detected=detected,
        target_lang=req.target_lang,
        story_bible=story_bible,
        engines={"extraction": "lossless_container_demux", "translation": trans_engine},
    )


@app.post("/v1/audio-to-subtitles", response_model=SubtitleProjectResponse)
async def audio_chunks_to_subtitles(
    manifest_json: str = Form(..., description="JSON array of AudioChunkDescriptor"),
    source_lang: str = Form("auto"),
    target_lang: str = Form("en"),
    style: TranslationStyle = Form(TranslationStyle.CINEMA_NATURAL),
    isolate_vocals: bool = Form(True),
    include_songs: bool = Form(True),
    is_portrait_video: bool = Form(False),
    foreign_dialogue_only_lang: Optional[str] = Form(None),
    gemini_api_key_override: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
) -> SubtitleProjectResponse:
    """
    Feature 1: Accepts silence-aligned 10-minute 16kHz Opus chunks (~1.1 MB each) from the phone,
    isolates vocals, routes per-language ASR in parallel with 0.0ms timestamp drift, runs
    Two-Pass Story Bible Translation, and returns ready-to-merge universal subtitles.
    """
    settings = get_settings()
    workdir = Path(tempfile.mkdtemp(prefix="subforge_audio_job_"))
    try:
        raw_manifest = json.loads(manifest_json)
        descriptors = [AudioChunkDescriptor(**item) for item in raw_manifest]
        desc_by_name = {d.filename: d for d in descriptors}

        saved_chunks: List[Tuple[int, int, str]] = []
        for idx, upload in enumerate(files):
            fname = upload.filename or f"chunk_{idx}.ogg"
            dest = workdir / os.path.basename(fname)
            with open(dest, "wb") as out_f:
                shutil.copyfileobj(upload.file, out_f)
            desc = desc_by_name.get(fname) or (
                descriptors[idx] if idx < len(descriptors) else AudioChunkDescriptor(
                    chunk_index=idx, offset_ms=idx * 600_000, duration_ms=600_000, filename=fname
                )
            )
            saved_chunks.append((desc.chunk_index, desc.offset_ms, str(dest)))

        asr = UniversalAsrRouter(settings)
        source_cues, detected_lang, asr_engine = await asr.transcribe_chunks_parallel(
            chunks=saved_chunks,
            source_lang=source_lang,
            isolate_vocals=isolate_vocals,
            include_songs=include_songs,
            gemini_key_override=gemini_api_key_override,
        )

        translator = UniversalStoryBibleTranslator(settings)
        translated_cues, story_bible, trans_engine = await translator.translate_movie_cues(
            cues=source_cues,
            source_lang=detected_lang if source_lang == "auto" else source_lang,
            target_lang=target_lang,
            style=style,
            is_portrait=is_portrait_video,
            foreign_dialogue_only_lang=foreign_dialogue_only_lang,
            gemini_key_override=gemini_api_key_override,
        )

        return _build_project_response(
            cues=translated_cues,
            source_detected=detected_lang,
            target_lang=target_lang,
            story_bible=story_bible,
            engines={"asr": asr_engine, "translation": trans_engine},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/v1/hardsub-sheets-to-subtitles", response_model=SubtitleProjectResponse)
async def hardsub_contact_sheets_to_subtitles(
    manifests_json: str = Form(..., description="JSON array of ContactSheetManifest"),
    source_lang: str = Form("auto"),
    target_lang: str = Form("en"),
    style: TranslationStyle = Form(TranslationStyle.CINEMA_NATURAL),
    is_portrait_video: bool = Form(False),
    gemini_api_key_override: Optional[str] = Form(None),
    sheets: List[UploadFile] = File(...),
) -> SubtitleProjectResponse:
    """
    Feature 2C: Accepts compact numbered 20-strip hardcoded subtitle contact sheets (~4 MB/movie),
    runs 110+ script Multimodal Vision-OCR + temporal fuzzy deduplication, preserves normalized
    bounding boxes for Dynamic Dark Pill Masking, and runs Story Bible Context Translation.
    """
    settings = get_settings()
    workdir = Path(tempfile.mkdtemp(prefix="subforge_ocr_job_"))
    try:
        manifests = [ContactSheetManifest(**m) for m in json.loads(manifests_json)]
        if len(manifests) != len(sheets):
            raise HTTPException(
                status_code=400,
                detail=f"Manifest count ({len(manifests)}) must match uploaded sheet images ({len(sheets)}).",
            )

        paired: List[Tuple[ContactSheetManifest, str]] = []
        for manifest, upload in zip(manifests, sheets):
            ext = Path(upload.filename or "sheet.webp").suffix or ".webp"
            dest = workdir / f"sheet_{manifest.sheet_index}{ext}"
            with open(dest, "wb") as out_f:
                shutil.copyfileobj(upload.file, out_f)
            paired.append((manifest, str(dest)))

        ocr = UniversalHardSubOcrEngine(settings)
        ocr_cues, detected_lang = await ocr.extract_from_contact_sheets(
            sheets=paired,
            source_lang_hint=source_lang,
            gemini_key_override=gemini_api_key_override,
        )

        translator = UniversalStoryBibleTranslator(settings)
        translated_cues, story_bible, trans_engine = await translator.translate_movie_cues(
            cues=ocr_cues,
            source_lang=detected_lang if source_lang == "auto" else source_lang,
            target_lang=target_lang,
            style=style,
            is_portrait=is_portrait_video,
            gemini_key_override=gemini_api_key_override,
        )

        return _build_project_response(
            cues=translated_cues,
            source_detected=detected_lang,
            target_lang=target_lang,
            story_bible=story_bible,
            engines={"ocr": "contact_sheet_multimodal_ocr", "translation": trans_engine},
            opaque_pill_box=True,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/v1/retranslate-cue", response_model=SubtitleCue)
async def retranslate_single_flagged_cue(req: RetranslateCueRequest) -> SubtitleCue:
    """One-tap Studio Timeline Editor endpoint to refine a single line with user context notes."""
    settings = get_settings()
    translator = UniversalStoryBibleTranslator(settings)
    try:
        return await translator.retranslate_single_cue(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
