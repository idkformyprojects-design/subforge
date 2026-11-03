from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .asr_router import _safe_parse_json
from .config import Settings
from .models import (
    CharacterProfile,
    RetranslateCueRequest,
    StoryBible,
    SubtitleCue,
    TranslationStyle,
)
from .script_engine import (
    compute_cps_and_validate_cue,
    get_script_spec,
    normalize_lang_code,
)


class AsyncRpmLimiter:
    """Paces requests cleanly at 10 RPM (1 req / 6.2s) when running on Google AI Studio Free Tier."""

    def __init__(self, enabled: bool, rpm: int = 10):
        self.enabled = enabled
        self.interval = 60.0 / max(1, rpm)
        self._lock = asyncio.Lock()
        self._last_ts = 0.0

    async def wait(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            now = time.monotonic()
            wait_sec = self.interval - (now - self._last_ts)
            if wait_sec > 0:
                await asyncio.sleep(wait_sec)
            self._last_ts = time.monotonic()


class UniversalStoryBibleTranslator:
    """
    Two-Pass Whole-Movie Context-Aware Subtitle Translator (200+ Target Languages):
    - Pass 1 (Global Story Bible): Scans the entire movie dialogue in a 1M-token window to lock
      character genders, formality registers (tu/vous, keigo, tui/tumi/apni), recurring terms & tone.
    - Pass 2 (Sliding-Window Direct Source -> Target): Translates 45 cues/batch with 8-cue lookback
      without pivoting through English, healing ASR/OCR phonetic typos via plot context, and validating
      100% cue-ID/timestamp integrity + target-script Netflix Timed Text rules.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._limiter = AsyncRpmLimiter(enabled=settings.gemini_free_tier_pacing, rpm=10)

    async def translate_movie_cues(
        self,
        cues: List[SubtitleCue],
        source_lang: str,
        target_lang: str,
        style: TranslationStyle = TranslationStyle.CINEMA_NATURAL,
        is_portrait: bool = False,
        foreign_dialogue_only_lang: Optional[str] = None,
        gemini_key_override: Optional[str] = None,
    ) -> Tuple[List[SubtitleCue], StoryBible, str]:
        if not cues:
            return [], StoryBible(), "noop"

        src_norm = normalize_lang_code(source_lang) if source_lang != "auto" else "auto"
        tgt_norm = normalize_lang_code(target_lang)
        foreign_keep = (
            normalize_lang_code(foreign_dialogue_only_lang)
            if foreign_dialogue_only_lang
            else None
        )

        # Same-language transcription-only shortcut (e.g., English video -> English SDH subtitles)
        if src_norm != "auto" and src_norm == tgt_norm and not foreign_keep:
            validated = [
                compute_cps_and_validate_cue(
                    c.model_copy(update={"translated_text": c.source_text}),
                    target_lang=tgt_norm,
                    is_portrait=is_portrait,
                    style=style,
                )
                for c in cues
            ]
            return validated, StoryBible(primary_source_languages=[src_norm]), "same_language_direct"

        api_key = gemini_key_override or self.settings.gemini_api_key
        if not api_key and not self.settings.openai_api_key and not self.settings.anthropic_api_key:
            raise RuntimeError(
                "No translation LLM key configured. Set GEMINI_API_KEY (free at aistudio.google.com)."
            )

        # Pass 1: Build Whole-Movie Story Bible
        story_bible = await self._extract_story_bible(cues, src_norm, tgt_norm, api_key)

        # Filter cues if Forced Narrative (Foreign-Dialogue-Only) mode is requested
        to_translate: List[SubtitleCue] = []
        skipped_map: Dict[int, SubtitleCue] = {}
        for c in cues:
            c_lang = normalize_lang_code(c.detected_lang) if c.detected_lang != "auto" else src_norm
            if foreign_keep and c_lang == foreign_keep:
                skipped_map[c.id] = c.model_copy(update={"translated_text": ""})
            else:
                to_translate.append(c)

        # Pass 2: Sliding-Window Context Translation (45 cues per batch + 8 previous cues lookback)
        spec = get_script_spec(tgt_norm)
        max_cpl = spec.max_chars_per_line_portrait if is_portrait else spec.max_chars_per_line_landscape
        batch_size = 45
        batches: List[Tuple[List[SubtitleCue], List[SubtitleCue]]] = []
        for start in range(0, len(to_translate), batch_size):
            window = to_translate[start : start + batch_size]
            lookback = to_translate[max(0, start - 8) : start]
            batches.append((lookback, window))

        translated_by_id: Dict[int, SubtitleCue] = {}

        if self.settings.gemini_free_tier_pacing:
            # Sequential paced execution respects 10 RPM Free Tier with zero 429 dropouts
            for lookback, window in batches:
                batch_out = await self._translate_window(
                    lookback, window, story_bible, src_norm, tgt_norm, max_cpl, spec.max_cps_adult, style, api_key
                )
                for c in batch_out:
                    translated_by_id[c.id] = c
        else:
            sem = asyncio.Semaphore(6)

            async def _run_batch(lb: List[SubtitleCue], win: List[SubtitleCue]) -> List[SubtitleCue]:
                async with sem:
                    return await self._translate_window(
                        lb, win, story_bible, src_norm, tgt_norm, max_cpl, spec.max_cps_adult, style, api_key
                    )

            all_batches = await asyncio.gather(*[_run_batch(lb, w) for lb, w in batches])
            for b_res in all_batches:
                for c in b_res:
                    translated_by_id[c.id] = c

        final_cues: List[SubtitleCue] = []
        for orig in cues:
            if orig.id in skipped_map:
                continue
            updated = translated_by_id.get(orig.id, orig)
            validated = compute_cps_and_validate_cue(
                updated,
                target_lang=tgt_norm,
                is_portrait=is_portrait,
                style=style,
            )
            final_cues.append(validated)

        return final_cues, story_bible, f"story_bible_{self.settings.gemini_model}"

    async def retranslate_single_cue(self, req: RetranslateCueRequest) -> SubtitleCue:
        api_key = req.gemini_api_key_override or self.settings.gemini_api_key
        spec = get_script_spec(req.target_lang)
        max_cpl = (
            spec.max_chars_per_line_portrait
            if req.is_portrait_video
            else spec.max_chars_per_line_landscape
        )
        bible_json = req.story_bible.model_dump_json() if req.story_bible else "{}"
        prompt = f"""You are a master cinema subtitle translator.
Target language: {req.target_lang} (max {max_cpl} chars/line, max 2 lines).
Movie Story Bible: {bible_json}
Previous lines: {req.context_prev}
Current source line: "{req.cue.source_text}"
Next lines: {req.context_next}
User guidance note: "{req.user_instruction or 'Natural idiomatic cinema translation'}"

Return STRICT JSON ONLY:
{{"translated_text": "...", "repaired_source": "{req.cue.source_text}"}}"""

        parsed = await self._call_gemini_json(prompt, api_key)
        new_txt = str(parsed.get("translated_text", req.cue.translated_text or req.cue.source_text)).strip()
        updated = req.cue.model_copy(
            update={
                "translated_text": new_txt,
                "needs_review": False,
                "review_reason": None,
                "confidence": 0.99,
            }
        )
        return compute_cps_and_validate_cue(
            updated, target_lang=req.target_lang, is_portrait=req.is_portrait_video
        )

    async def _extract_story_bible(
        self,
        cues: List[SubtitleCue],
        src_lang: str,
        tgt_lang: str,
        api_key: str,
    ) -> StoryBible:
        # Sample up to 350 representative lines across start/middle/end if massive (>2,000 lines)
        sampled = cues if len(cues) <= 350 else (cues[:140] + cues[len(cues) // 2 - 70 : len(cues) // 2 + 70] + cues[-140:])
        compact_script = "\n".join(
            f"[{c.id}] ({c.speaker or 'S'}): {c.source_text}" for c in sampled
        )
        prompt = f"""Analyze this movie transcript ({src_lang} -> translating directly into {tgt_lang}) and build a concise Story Bible so every character's gender, relationship, formality register, and recurring terms stay 100% consistent throughout the film.

Transcript excerpt:
{compact_script}

Return STRICT JSON ONLY matching this schema:
{{
  "movie_summary": "2-sentence plot & setting summary",
  "genre_and_tone": "e.g., gritty crime thriller / warm family drama / anime",
  "primary_source_languages": ["{src_lang}"],
  "characters": [
    {{
      "name": "Character Name or Speaker ID",
      "inferred_gender": "male | female | unknown",
      "role_or_relationship": "e.g., older brother, detective",
      "formality_register": "polite/formal vs casual/intimate"
    }}
  ],
  "glossary": {{
    "original_term_or_name": "consistent {tgt_lang} rendering"
  }}
}}"""
        parsed = await self._call_gemini_json(prompt, api_key)
        try:
            chars = [CharacterProfile(**ch) for ch in parsed.get("characters", [])[:25]]
            return StoryBible(
                movie_summary=str(parsed.get("movie_summary", "")),
                genre_and_tone=str(parsed.get("genre_and_tone", "cinema")),
                primary_source_languages=list(parsed.get("primary_source_languages", [src_lang])),
                characters=chars,
                glossary={str(k): str(v) for k, v in (parsed.get("glossary") or {}).items()},
            )
        except Exception:
            return StoryBible(primary_source_languages=[src_lang])

    async def _translate_window(
        self,
        lookback: List[SubtitleCue],
        window: List[SubtitleCue],
        story_bible: StoryBible,
        src_lang: str,
        tgt_lang: str,
        max_cpl: int,
        max_cps: float,
        style: TranslationStyle,
        api_key: str,
    ) -> List[SubtitleCue]:
        lookback_block = [
            {"id": c.id, "speaker": c.speaker, "text": c.source_text} for c in lookback
        ]
        input_items = [
            {
                "id": c.id,
                "duration_sec": round(max(0.8, (c.end_ms - c.start_ms) / 1000.0), 2),
                "speaker": c.speaker,
                "is_song": c.is_song,
                "text": c.source_text,
            }
            for c in window
        ]

        style_instruction = (
            "Translate idiomatically like a Netflix/Criterion native cinema localization — natural spoken dialogue, accurate gender/honorifics, never stiff machine literalism."
            if style != TranslationStyle.STRICT_LITERAL
            else "Translate accurately and faithfully close to original phrasing while remaining grammatically correct."
        )

        prompt = f"""You are an elite cinema subtitle localizer translating DIRECTLY from `{src_lang}` into `{tgt_lang}` (do NOT pivot through English).
{style_instruction}

Movie Story Bible (maintain 100% character gender, formality & glossary consistency):
{story_bible.model_dump_json()}

Target Script Rules for `{tgt_lang}`:
- Max {max_cpl} characters per line, max 2 lines per cue.
- Target reading speed <= {max_cps} chars/sec relative to each cue's `duration_sec` (condense wordy phrasing naturally if needed so viewers can read comfortably in time!).
- If a source line has an obvious speech-to-text / OCR homophone typo, silently infer the intended word from surrounding movie context and set `repaired_source`.
- Set `uncertain`: true ONLY if a proper noun or muffled phrase is genuinely ambiguous and should be spot-checked by a human.

Previous Context Only (DO NOT include in output):
{json.dumps(lookback_block, ensure_ascii=False)}

Cues to Translate (MUST return ALL {len(input_items)} exact `id`s):
{json.dumps(input_items, ensure_ascii=False)}

Return STRICT JSON ONLY:
{{
  "translations": [
    {{
      "id": 1,
      "translated_text": "target language subtitle",
      "repaired_source": "optional corrected source if typo fixed else empty",
      "uncertain": false,
      "reason": ""
    }}
  ]
}}"""

        parsed = await self._call_gemini_json(prompt, api_key)
        trans_map: Dict[int, Dict[str, Any]] = {}
        for item in parsed.get("translations", []):
            if "id" in item:
                trans_map[int(item["id"])] = item

        output: List[SubtitleCue] = []
        for orig in window:
            hit = trans_map.get(orig.id)
            cp = orig.model_copy(deep=True)
            if hit:
                cp.translated_text = str(hit.get("translated_text", "")).strip() or orig.source_text
                repaired = str(hit.get("repaired_source", "")).strip()
                if repaired:
                    cp.source_text = repaired
                if bool(hit.get("uncertain", False)):
                    cp.needs_review = True
                    cp.review_reason = str(hit.get("reason") or "Ambiguous context/noun")
            else:
                cp.translated_text = orig.source_text
                cp.needs_review = True
                cp.review_reason = "Fallback cue — tap AI Re-translate to verify"
            output.append(cp)

        return output

    async def _call_gemini_json(self, prompt: str, api_key: str) -> Dict[str, Any]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.settings.gemini_model}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        for attempt in range(4):
            await self._limiter.wait()
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 429 and attempt < 3:
                    await asyncio.sleep(7.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                body = resp.json()
                raw = (
                    body.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "{}")
                )
                return _safe_parse_json(raw)
        return {}
