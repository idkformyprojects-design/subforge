from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .asr_vocal_separator import isolate_dialogue_vocals
from .config import Settings
from .models import SubtitleCue, WordTiming
from .script_engine import INDIC_ABUGIDA_LANGS, normalize_lang_code


def _parse_mmss_or_hms_to_ms(ts: Any) -> int:
    if isinstance(ts, (int, float)):
        # Heuristic: values > 1000 likely already ms unless small seconds float
        return int(ts) if isinstance(ts, int) and ts > 500 else int(float(ts) * 1000)
    s = str(ts).strip().replace(",", ".")
    if s.isdigit():
        return int(s)
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int((int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])) * 1000)
        if len(parts) == 2:
            return int((int(parts[0]) * 60 + float(parts[1])) * 1000)
        return int(float(s) * 1000)
    except ValueError:
        return 0


class UniversalAsrRouter:
    """
    Universal Multi-Engine Speech-to-Text Router supporting 125+ to 1,600+ languages:
    - Routes South Asian languages to Sarvam Saaras / IndicConformer when configured
    - Routes 99 global languages to ElevenLabs Scribe v1/v2 or Local/Modal faster-whisper
    - Falls back seamlessly to Google Gemini 2.5 Flash Multimodal Audio ($0 Free Tier, 100+ langs,
      per-cue language ID & code-switching!) or Groq Whisper large-v3 ($0 Free Tier).
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def select_best_engine(self, source_lang: str, gemini_key_override: Optional[str] = None) -> str:
        norm = normalize_lang_code(source_lang) if source_lang != "auto" else "auto"

        if norm in INDIC_ABUGIDA_LANGS and self.settings.sarvam_api_key:
            return "sarvam_saaras"
        if self.settings.elevenlabs_api_key:
            return "elevenlabs_scribe"
        if self.settings.enable_local_whisperx:
            return "local_whisperx"
        if gemini_key_override or self.settings.gemini_api_key:
            return "gemini_multimodal_asr"
        if self.settings.groq_api_key:
            return "groq_whisper"
        raise RuntimeError(
            "No ASR engine configured. Provide GEMINI_API_KEY (free at aistudio.google.com), "
            "GROQ_API_KEY, ELEVENLABS_API_KEY, SARVAM_API_KEY, or enable_local_whisperx on Modal."
        )

    async def transcribe_chunks_parallel(
        self,
        chunks: List[Tuple[int, int, str]],  # [(chunk_index, offset_ms, file_path), ...]
        source_lang: str = "auto",
        isolate_vocals: bool = True,
        include_songs: bool = True,
        gemini_key_override: Optional[str] = None,
    ) -> Tuple[List[SubtitleCue], str, str]:
        """
        Transcribes all silence-aligned audio chunks concurrently (or paced on free tier)
        and stitches them with exact millisecond offsets (0.0ms cumulative drift).
        """
        engine = self.select_best_engine(source_lang, gemini_key_override)
        max_concurrency = (
            2
            if (engine == "gemini_multimodal_asr" and self.settings.gemini_free_tier_pacing)
            else 6
        )
        sem = asyncio.Semaphore(max_concurrency)

        async def _process_one(idx: int, offset_ms: int, path: str) -> Tuple[int, List[SubtitleCue], str]:
            async with sem:
                clean_path = await asyncio.to_thread(
                    isolate_dialogue_vocals,
                    path,
                    self.settings.enable_local_demucs if isolate_vocals else False,
                )
                if engine == "elevenlabs_scribe":
                    cues, det = await self._transcribe_elevenlabs_scribe(clean_path, source_lang)
                elif engine == "sarvam_saaras":
                    cues, det = await self._transcribe_sarvam(clean_path, source_lang)
                elif engine == "local_whisperx":
                    cues, det = await asyncio.to_thread(
                        self._transcribe_local_faster_whisper, clean_path, source_lang
                    )
                elif engine == "groq_whisper":
                    cues, det = await self._transcribe_groq_whisper(clean_path, source_lang)
                else:
                    cues, det = await self._transcribe_gemini_multimodal(
                        clean_path, source_lang, gemini_key_override
                    )

                shifted: List[SubtitleCue] = []
                for c in cues:
                    if not include_songs and c.is_song:
                        continue
                    c.start_ms += offset_ms
                    c.end_ms += offset_ms
                    for w in c.words:
                        w.start_ms += offset_ms
                        w.end_ms += offset_ms
                    shifted.append(c)
                return idx, shifted, det

        results = await asyncio.gather(
            *[_process_one(i, off, p) for (i, off, p) in chunks]
        )
        results.sort(key=lambda r: r[0])

        merged: List[SubtitleCue] = []
        lang_votes: Dict[str, int] = {}
        next_id = 1
        for _, chunk_cues, det_lang in results:
            if det_lang and det_lang != "auto":
                lang_votes[det_lang] = lang_votes.get(det_lang, 0) + max(1, len(chunk_cues))
            for cue in chunk_cues:
                # Add +60ms natural lead-in / +120ms trail-out without overlapping previous cue
                lead_start = max(0, cue.start_ms - 60)
                if merged and lead_start < merged[-1].end_ms + 40:
                    lead_start = merged[-1].end_ms + 40
                cue.start_ms = lead_start
                cue.end_ms = max(cue.start_ms + 850, cue.end_ms + 120)
                cue.id = next_id
                next_id += 1
                merged.append(cue)

        primary_lang = (
            max(lang_votes.items(), key=lambda kv: kv[1])[0]
            if lang_votes
            else (source_lang if source_lang != "auto" else "en")
        )
        return merged, primary_lang, engine

    async def _transcribe_gemini_multimodal(
        self,
        audio_path: str,
        source_lang: str,
        key_override: Optional[str] = None,
    ) -> Tuple[List[SubtitleCue], str]:
        """
        Uses Gemini 2.5 Flash multimodal audio understanding (100+ languages, free tier supported)
        to extract verbatim original-script dialogue cues, timestamps, speakers, song tags, and
        per-cue detected language codes (supporting multi-language code-switched movies!).
        """
        api_key = key_override or self.settings.gemini_api_key
        audio_bytes = Path(audio_path).read_bytes()
        mime_type = mimetypes.guess_type(audio_path)[0] or "audio/ogg"
        if audio_path.endswith(".opus") or audio_path.endswith(".ogg"):
            mime_type = "audio/ogg"
        elif audio_path.endswith(".mp3"):
            mime_type = "audio/mp3"
        elif audio_path.endswith(".wav"):
            mime_type = "audio/wav"
        elif audio_path.endswith(".m4a") or audio_path.endswith(".aac"):
            mime_type = "audio/mp4"

        lang_hint = (
            f"Expected primary language: {source_lang} (still detect any code-switched scenes)."
            if source_lang and source_lang != "auto"
            else "Auto-detect the spoken language(s) per utterance accurately."
        )

        prompt = f"""You are a precision cinema speech-to-text aligner.
Transcribe ALL spoken dialogue in this audio clip VERBATIM in its original native script (do NOT translate yet).
{lang_hint}
Rules:
1. Ignore pure instrumental background music, noise, or silence — NEVER hallucinate filler phrases.
2. Split utterances into natural subtitle-sized cues (1 to 6 seconds each).
3. Provide accurate `start_ms` and `end_ms` relative to the start of this audio clip (0 ms).
4. Set `is_song` to true ONLY if the vocals are sung lyrics rather than spoken dialogue.
5. Set `confidence` between 0.50 and 0.99 (lower if mumbled, noisy, or uncertain proper noun).
6. Return STRICT JSON ONLY matching this exact schema:
{{
  "primary_lang": "iso-639-1 code e.g. en, bn, es, ja, ar, hi, fr",
  "cues": [
    {{
      "start_ms": 1200,
      "end_ms": 3850,
      "text": "original spoken words",
      "lang": "iso code",
      "speaker": "S1",
      "is_song": false,
      "confidence": 0.96
    }}
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
                                "mime_type": mime_type,
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                        {"text": prompt},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }

        async with httpx.AsyncClient(timeout=240.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        raw_text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "{}")
        )
        parsed = _safe_parse_json(raw_text)
        primary_lang = normalize_lang_code(parsed.get("primary_lang", source_lang or "en"))
        cues: List[SubtitleCue] = []
        for idx, item in enumerate(parsed.get("cues", []), start=1):
            txt = str(item.get("text", "")).strip()
            if not txt:
                continue
            s_ms = _parse_mmss_or_hms_to_ms(item.get("start_ms", 0))
            e_ms = _parse_mmss_or_hms_to_ms(item.get("end_ms", s_ms + 2000))
            conf = float(item.get("confidence", 0.92))
            cues.append(
                SubtitleCue(
                    id=idx,
                    start_ms=s_ms,
                    end_ms=max(s_ms + 500, e_ms),
                    source_text=txt,
                    detected_lang=normalize_lang_code(item.get("lang", primary_lang)),
                    speaker=item.get("speaker"),
                    is_song=bool(item.get("is_song", False)),
                    confidence=conf,
                    needs_review=conf < 0.85,
                )
            )
        return cues, primary_lang

    async def _transcribe_elevenlabs_scribe(
        self, audio_path: str, source_lang: str
    ) -> Tuple[List[SubtitleCue], str]:
        """ElevenLabs Scribe v1/v2 (99 languages, word-level timestamps & speaker diarization)."""
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {"xi-api-key": self.settings.elevenlabs_api_key}
        form_data: Dict[str, Any] = {
            "model_id": "scribe_v1",
            "diarize": "true",
            "tag_audio_events": "true",
        }
        if source_lang and source_lang != "auto":
            form_data["language_code"] = normalize_lang_code(source_lang)

        async with httpx.AsyncClient(timeout=240.0) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (os.path.basename(audio_path), f, "audio/ogg")}
                resp = await client.post(url, headers=headers, data=form_data, files=files)
                resp.raise_for_status()
                body = resp.json()

        det_lang = normalize_lang_code(body.get("language_code", source_lang or "en"))
        words_raw = body.get("words", [])
        return _group_words_into_subtitle_cues(words_raw, det_lang), det_lang

    async def _transcribe_groq_whisper(
        self, audio_path: str, source_lang: str
    ) -> Tuple[List[SubtitleCue], str]:
        """GroqCloud Whisper large-v3 turbo ($0 Free Tier)."""
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}"}
        data: Dict[str, Any] = {
            "model": "whisper-large-v3-turbo",
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if source_lang and source_lang != "auto":
            data["language"] = normalize_lang_code(source_lang)

        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (os.path.basename(audio_path), f, "audio/ogg")}
                resp = await client.post(url, headers=headers, data=data, files=files)
                resp.raise_for_status()
                body = resp.json()

        det_lang = normalize_lang_code(body.get("language", source_lang or "en"))
        cues: List[SubtitleCue] = []
        for idx, seg in enumerate(body.get("segments", []), start=1):
            txt = str(seg.get("text", "")).strip()
            if not txt:
                continue
            avg_logprob = float(seg.get("avg_logprob", -0.25))
            conf = max(0.50, min(0.99, round(1.0 + (avg_logprob * 0.35), 2)))
            cues.append(
                SubtitleCue(
                    id=idx,
                    start_ms=int(float(seg.get("start", 0.0)) * 1000),
                    end_ms=int(float(seg.get("end", 2.0)) * 1000),
                    source_text=txt,
                    detected_lang=det_lang,
                    confidence=conf,
                    needs_review=conf < 0.85,
                )
            )
        return cues, det_lang

    async def _transcribe_sarvam(
        self, audio_path: str, source_lang: str
    ) -> Tuple[List[SubtitleCue], str]:
        """Sarvam AI Saaras/Saarika specialist Indic STT with timestamps."""
        url = "https://api.sarvam.ai/speech-to-text"
        headers = {"api-subscription-key": self.settings.sarvam_api_key}
        bcp_map = {
            "bn": "bn-IN", "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN",
            "ml": "ml-IN", "kn": "kn-IN", "mr": "mr-IN", "gu": "gu-IN",
            "pa": "pa-IN", "or": "od-IN",
        }
        norm = normalize_lang_code(source_lang)
        data = {
            "model": "saarika:v2.5",
            "with_timestamps": "true",
            "language_code": bcp_map.get(norm, "unknown"),
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (os.path.basename(audio_path), f, "audio/ogg")}
                resp = await client.post(url, headers=headers, data=data, files=files)
                resp.raise_for_status()
                body = resp.json()

        det_lang = normalize_lang_code(body.get("language_code", norm or "hi"))
        timestamps = body.get("timestamps", {})
        words = timestamps.get("words", [])
        starts = timestamps.get("start_time_seconds", [])
        ends = timestamps.get("end_time_seconds", [])
        if words and len(words) == len(starts):
            word_objs = [
                {"text": w, "start": s, "end": e, "type": "word"}
                for w, s, e in zip(words, starts, ends)
            ]
            return _group_words_into_subtitle_cues(word_objs, det_lang), det_lang

        transcript = str(body.get("transcript", "")).strip()
        if not transcript:
            return [], det_lang
        return [
            SubtitleCue(
                id=1,
                start_ms=0,
                end_ms=4000,
                source_text=transcript,
                detected_lang=det_lang,
                confidence=0.93,
            )
        ], det_lang

    def _transcribe_local_faster_whisper(
        self, audio_path: str, source_lang: str
    ) -> Tuple[List[SubtitleCue], str]:
        """Runs faster-whisper large-v3-turbo with Silero VAD & word timestamps on Modal Free GPU."""
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel("large-v3-turbo", device="auto", compute_type="int8_float16")
        lang_arg = None if source_lang in ("", "auto") else normalize_lang_code(source_lang)
        segments, info = model.transcribe(
            audio_path,
            language=lang_arg,
            vad_filter=True,
            word_timestamps=True,
        )
        det_lang = normalize_lang_code(info.language or source_lang or "en")
        cues: List[SubtitleCue] = []
        for idx, seg in enumerate(segments, start=1):
            txt = seg.text.strip()
            if not txt:
                continue
            words = [
                WordTiming(
                    word=w.word.strip(),
                    start_ms=int(w.start * 1000),
                    end_ms=int(w.end * 1000),
                    confidence=round(float(w.probability), 2),
                )
                for w in (seg.words or [])
            ]
            conf = (
                round(sum(w.confidence for w in words) / len(words), 2)
                if words
                else 0.90
            )
            cues.append(
                SubtitleCue(
                    id=idx,
                    start_ms=int(seg.start * 1000),
                    end_ms=int(seg.end * 1000),
                    source_text=txt,
                    detected_lang=det_lang,
                    confidence=conf,
                    needs_review=conf < 0.85,
                    words=words,
                )
            )
        return cues, det_lang


def _group_words_into_subtitle_cues(
    words_raw: List[Dict[str, Any]], det_lang: str
) -> List[SubtitleCue]:
    cues: List[SubtitleCue] = []
    bucket: List[WordTiming] = []
    cur_speaker: Optional[str] = None
    SENTENCE_ENDS = (".", "?", "!", "।", "。", "？", "！", "؟")

    def flush() -> None:
        nonlocal bucket, cur_speaker
        if not bucket:
            return
        joiner = "" if det_lang in {"ja", "zh", "th", "lo", "km", "my"} else " "
        text = joiner.join(w.word for w in bucket).strip()
        text = re.sub(r"\s+([,.;:?!])", r"\1", text)
        if text:
            avg_conf = round(sum(w.confidence for w in bucket) / len(bucket), 2)
            cues.append(
                SubtitleCue(
                    id=len(cues) + 1,
                    start_ms=bucket[0].start_ms,
                    end_ms=max(bucket[0].start_ms + 600, bucket[-1].end_ms),
                    source_text=text,
                    detected_lang=det_lang,
                    speaker=cur_speaker,
                    confidence=avg_conf,
                    needs_review=avg_conf < 0.85,
                    words=list(bucket),
                )
            )
        bucket = []

    for item in words_raw:
        if item.get("type") == "spacing":
            continue
        w_txt = str(item.get("text", "")).strip()
        if not w_txt:
            continue
        s_ms = int(float(item.get("start", 0.0)) * 1000)
        e_ms = int(float(item.get("end", s_ms / 1000.0 + 0.3)) * 1000)
        spk = item.get("speaker_id")
        conf = float(item.get("logprob", 0.0))
        prob = round(min(0.99, max(0.55, 1.0 + conf * 0.25)) if conf < 0 else 0.95, 2)

        # Break cue on speaker change, >650ms pause, >5.5s span, or sentence boundary after 2.2s
        if bucket:
            pause_ms = s_ms - bucket[-1].end_ms
            span_ms = e_ms - bucket[0].start_ms
            prev_ended = bucket[-1].word.endswith(SENTENCE_ENDS)
            if (
                (spk and cur_speaker and spk != cur_speaker)
                or pause_ms > 650
                or span_ms > 5500
                or (prev_ended and span_ms >= 2000)
                or len(bucket) >= 14
            ):
                flush()

        cur_speaker = spk or cur_speaker
        bucket.append(WordTiming(word=w_txt, start_ms=s_ms, end_ms=e_ms, confidence=prob))

    flush()
    return cues


def _safe_parse_json(raw: str) -> Dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return {}
