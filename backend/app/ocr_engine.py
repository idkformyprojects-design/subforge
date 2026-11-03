from __future__ import annotations

import asyncio
import base64
import difflib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from .asr_router import _safe_parse_json
from .config import Settings
from .models import BoundingBoxNorm, ContactSheetManifest, SubtitleCue
from .script_engine import normalize_lang_code


class UniversalHardSubOcrEngine:
    """
    Decodes hardcoded (burned-in) subtitles across 110+ world scripts (Latin, CJK including
    vertical Japanese, RTL Arabic/Hebrew/Urdu, Cyrillic, Thai, South Asian Abugidas, stylized fonts)
    from compact 20-strip numbered contact sheets generated on the Android phone.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def extract_from_contact_sheets(
        self,
        sheets: List[Tuple[ContactSheetManifest, str]],  # [(manifest, image_path), ...]
        source_lang_hint: str = "auto",
        gemini_key_override: Optional[str] = None,
    ) -> Tuple[List[SubtitleCue], str]:
        api_key = gemini_key_override or self.settings.gemini_api_key
        if not api_key:
            raise RuntimeError(
                "Hardcoded subtitle Vision-OCR requires GEMINI_API_KEY (free at aistudio.google.com)."
            )

        max_conc = 2 if self.settings.gemini_free_tier_pacing else 6
        sem = asyncio.Semaphore(max_conc)

        async def _ocr_sheet(
            manifest: ContactSheetManifest, img_path: str
        ) -> Tuple[int, List[SubtitleCue], str]:
            async with sem:
                cues, lang = await self._ocr_single_sheet_gemini(
                    manifest, img_path, source_lang_hint, api_key
                )
                return manifest.sheet_index, cues, lang

        results = await asyncio.gather(*[_ocr_sheet(m, p) for (m, p) in sheets])
        results.sort(key=lambda r: r[0])

        raw_cues: List[SubtitleCue] = []
        lang_counts: Dict[str, int] = {}
        for _, sheet_cues, det_lang in results:
            if det_lang and det_lang != "auto":
                lang_counts[det_lang] = lang_counts.get(det_lang, 0) + max(1, len(sheet_cues))
            raw_cues.extend(sheet_cues)

        deduped = deduplicate_temporal_ocr_cues(raw_cues)
        detected = (
            max(lang_counts.items(), key=lambda kv: kv[1])[0]
            if lang_counts
            else (source_lang_hint if source_lang_hint != "auto" else "en")
        )
        return deduped, detected

    async def _ocr_single_sheet_gemini(
        self,
        manifest: ContactSheetManifest,
        img_path: str,
        source_lang_hint: str,
        api_key: str,
    ) -> Tuple[List[SubtitleCue], str]:
        img_bytes = Path(img_path).read_bytes()
        mime = "image/webp" if img_path.endswith(".webp") else "image/jpeg"
        if img_path.endswith(".png"):
            mime = "image/png"

        strip_ids = [s.strip_id for s in manifest.strips]
        hint_txt = (
            f"Expected subtitle script/language: {source_lang_hint}."
            if source_lang_hint != "auto"
            else "Auto-detect the script/language (any of 110+ world scripts)."
        )

        prompt = f"""You are a high-precision video hardcoded-subtitle OCR engine.
This contact sheet contains {len(strip_ids)} horizontally stacked video subtitle crops labeled with strip IDs {strip_ids} on the left badge.
{hint_txt}
Instructions:
1. Read ONLY the subtitle dialogue text inside each numbered strip (ignore channel watermarks, logos, or background street signs).
2. If a strip has NO subtitle text, return an empty string "" for that strip_id.
3. Preserve exact original language characters & punctuation.
4. Provide `tight_bbox` as normalized [x, y, width, height] (0.0 to 1.0 within that strip) tightly enclosing the old burned-in text if visible.
5. Return STRICT JSON ONLY:
{{
  "detected_lang": "iso-639-1 code",
  "items": [
    {{"strip_id": 1, "text": "recognized subtitle text", "confidence": 0.98}}
  ]
}}"""

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.settings.gemini_model}:generateContent?key={api_key}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(img_bytes).decode("ascii"),
                            }
                        },
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()

        raw_txt = (
            body.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )
        parsed = _safe_parse_json(raw_txt)
        det_lang = normalize_lang_code(parsed.get("detected_lang", source_lang_hint or "en"))

        meta_by_id = {s.strip_id: s for s in manifest.strips}
        cues: List[SubtitleCue] = []
        for item in parsed.get("items", []):
            sid = int(item.get("strip_id", -1))
            txt = str(item.get("text", "")).strip()
            if sid not in meta_by_id or not txt:
                continue
            meta = meta_by_id[sid]
            conf = float(item.get("confidence", 0.94))
            cues.append(
                SubtitleCue(
                    id=sid,
                    start_ms=meta.start_ms,
                    end_ms=meta.end_ms,
                    source_text=txt,
                    detected_lang=det_lang,
                    confidence=conf,
                    needs_review=conf < 0.85,
                    mask_box=meta.mask_box,
                )
            )
        return cues, det_lang


def deduplicate_temporal_ocr_cues(cues: List[SubtitleCue]) -> List[SubtitleCue]:
    """
    Merges consecutive video frames showing the same burned-in subtitle (even across slight
    motion-blur / fade-in OCR jitter using normalized similarity >= 0.86) into single clean cues.
    """
    if not cues:
        return []
    ordered = sorted(cues, key=lambda c: c.start_ms)
    merged: List[SubtitleCue] = []

    for cue in ordered:
        txt = cue.source_text.strip()
        if not txt:
            continue
        if not merged:
            merged.append(cue.model_copy(deep=True))
            continue

        prev = merged[-1]
        gap_ms = cue.start_ms - prev.end_ms
        sim = difflib.SequenceMatcher(None, prev.source_text.lower(), txt.lower()).ratio()

        if gap_ms <= 450 and sim >= 0.86:
            prev.end_ms = max(prev.end_ms, cue.end_ms)
            # Keep whichever frame had higher OCR confidence (avoids fade-in partial glyph errors)
            if cue.confidence > prev.confidence or (
                cue.confidence == prev.confidence and len(txt) > len(prev.source_text)
            ):
                prev.source_text = txt
                prev.confidence = cue.confidence
                prev.needs_review = cue.confidence < 0.85
            if prev.mask_box and cue.mask_box:
                min_x = min(prev.mask_box.x, cue.mask_box.x)
                min_y = min(prev.mask_box.y, cue.mask_box.y)
                max_r = max(prev.mask_box.x + prev.mask_box.width, cue.mask_box.x + cue.mask_box.width)
                max_b = max(prev.mask_box.y + prev.mask_box.height, cue.mask_box.y + cue.mask_box.height)
                prev.mask_box = BoundingBoxNorm(
                    x=min_x,
                    y=min_y,
                    width=min(1.0 - min_x, max_r - min_x),
                    height=min(1.0 - min_y, max_b - min_y),
                )
        else:
            merged.append(cue.model_copy(deep=True))

    for i, c in enumerate(merged, start=1):
        c.id = i
    return merged
