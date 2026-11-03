from __future__ import annotations

import re
from typing import List

from .models import ScriptFamily, SubtitleCue
from .script_engine import get_script_spec


SRT_TIME_RE = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2}):(?P<ss>\d{2})[,.](?P<sms>\d{1,3})\s*-->\s*"
    r"(?P<eh>\d{1,2}):(?P<em_>\d{2}):(?P<es>\d{2})[,.](?P<ems>\d{1,3})"
)

ASS_DIALOGUE_RE = re.compile(
    r"^Dialogue:\s*\d+,(?P<start>\d+:\d{2}:\d{2}\.\d{1,3}),(?P<end>\d+:\d{2}:\d{2}\.\d{1,3}),"
    r"(?P<style>[^,]*),(?P<name>[^,]*),[^,]*,[^,]*,[^,]*,[^,]*,(?P<text>.*)$"
)


def _hms_to_ms(h: int, m: int, s: int, ms_str: str) -> int:
    ms = int(ms_str.ljust(3, "0")[:3])
    return ((h * 3600) + (m * 60) + s) * 1000 + ms


def _ass_time_to_ms(ts: str) -> int:
    parts = ts.strip().split(":")
    h = int(parts[0])
    m = int(parts[1])
    sec_parts = parts[2].split(".")
    s = int(sec_parts[0])
    cs = sec_parts[1] if len(sec_parts) > 1 else "0"
    ms = int(cs.ljust(3, "0")[:3]) if len(cs) == 3 else int(cs.ljust(2, "0")[:2]) * 10
    return ((h * 3600) + (m * 60) + s) * 1000 + ms


def ms_to_srt_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    h = ms // 3_600_000
    rem = ms % 3_600_000
    m = rem // 60_000
    rem %= 60_000
    s = rem // 1000
    millis = rem % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def ms_to_vtt_timestamp(ms: int) -> str:
    return ms_to_srt_timestamp(ms).replace(",", ".")


def ms_to_ass_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    h = ms // 3_600_000
    rem = ms % 3_600_000
    m = rem // 60_000
    rem %= 60_000
    s = rem // 1000
    cs = (rem % 1000) // 10
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def strip_subtitle_markup(raw: str) -> str:
    text = raw.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    # Remove ASS override blocks like {\an8\pos(400,50)}
    text = re.sub(r"\{\\[^}]*\}", "", text)
    # Remove HTML/VTT tags like <i>, <b>, <c.yellow>, <font color="...">
    text = re.sub(r"<[^>]+>", "", text)
    return "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())


def parse_subtitle_content(content: str, format_hint: str = "auto") -> List[SubtitleCue]:
    """
    Universal parser for embedded soft-subtitle streams: SRT, WebVTT, and ASS/SSA.
    Preserves exact millisecond timestamps and speaker metadata where present.
    """
    raw = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    fmt = format_hint.lower().strip()
    if fmt == "auto":
        if "[Events]" in raw or "Dialogue:" in raw:
            fmt = "ass"
        elif raw.lstrip().startswith("WEBVTT"):
            fmt = "vtt"
        else:
            fmt = "srt"

    cues: List[SubtitleCue] = []
    if fmt in ("ass", "ssa"):
        cue_id = 1
        for line in raw.splitlines():
            m = ASS_DIALOGUE_RE.match(line.strip())
            if not m:
                continue
            start_ms = _ass_time_to_ms(m.group("start"))
            end_ms = _ass_time_to_ms(m.group("end"))
            speaker = m.group("name").strip() or None
            clean_txt = strip_subtitle_markup(m.group("text"))
            if not clean_txt:
                continue
            cues.append(
                SubtitleCue(
                    id=cue_id,
                    start_ms=start_ms,
                    end_ms=max(start_ms + 300, end_ms),
                    source_text=clean_txt,
                    speaker=speaker,
                    confidence=1.0,
                )
            )
            cue_id += 1
        if cues:
            return cues

    # SRT / WebVTT block parser
    blocks = re.split(r"\n\s*\n", raw.strip())
    cue_id = 1
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines or lines[0].startswith("WEBVTT") or lines[0].startswith("NOTE"):
            continue

        time_idx = -1
        match = None
        for idx, ln in enumerate(lines[:3]):
            m = SRT_TIME_RE.search(ln)
            if m:
                time_idx = idx
                match = m
                break
        if match is None:
            continue

        start_ms = _hms_to_ms(
            int(match.group("sh")),
            int(match.group("sm")),
            int(match.group("ss")),
            match.group("sms"),
        )
        end_ms = _hms_to_ms(
            int(match.group("eh")),
            int(match.group("em_")),
            int(match.group("es")),
            match.group("ems"),
        )
        text_body = strip_subtitle_markup("\n".join(lines[time_idx + 1 :]))
        if not text_body:
            continue

        cues.append(
            SubtitleCue(
                id=cue_id,
                start_ms=start_ms,
                end_ms=max(start_ms + 300, end_ms),
                source_text=text_body,
                confidence=1.0,
            )
        )
        cue_id += 1

    return cues


def serialize_to_srt(cues: List[SubtitleCue]) -> str:
    blocks: List[str] = []
    for idx, cue in enumerate(cues, start=1):
        txt = (cue.translated_text or cue.source_text).strip()
        if not txt:
            continue
        blocks.append(
            f"{idx}\n{ms_to_srt_timestamp(cue.start_ms)} --> {ms_to_srt_timestamp(cue.end_ms)}\n{txt}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def serialize_to_vtt(cues: List[SubtitleCue]) -> str:
    blocks = ["WEBVTT", ""]
    for idx, cue in enumerate(cues, start=1):
        txt = (cue.translated_text or cue.source_text).strip()
        if not txt:
            continue
        blocks.append(
            f"{idx}\n{ms_to_vtt_timestamp(cue.start_ms)} --> {ms_to_vtt_timestamp(cue.end_ms)}\n{txt}\n"
        )
    return "\n".join(blocks).strip() + "\n"


def serialize_to_ass(
    cues: List[SubtitleCue],
    target_lang: str,
    opaque_pill_box: bool = False,
) -> str:
    """
    Generates cinema-grade SubStation Alpha (.ass) with target-script Noto Sans font selection,
    proper outline/shadow or opaque dark pill BorderStyle=3 (for covering hardcoded subs).
    """
    spec = get_script_spec(target_lang)
    font_map = {
        ScriptFamily.CJK_JAPANESE: "Noto Sans JP",
        ScriptFamily.CJK_CHINESE_KOREAN: "Noto Sans CJK SC",
        ScriptFamily.RTL_BIDI: "Noto Sans Arabic",
        ScriptFamily.SEA_UNSPACED: "Noto Sans Thai",
        ScriptFamily.INDIC_ABUGIDA: "Noto Sans",
        ScriptFamily.LATIN_CYRILLIC_GREEK: "Noto Sans",
        ScriptFamily.GLOBAL_OTHER: "Noto Sans",
    }
    font_name = font_map.get(spec.family, "Noto Sans")
    border_style = 3 if opaque_pill_box else 1
    back_colour = "&H26101014" if opaque_pill_box else "&H80000000"
    outline_px = 14 if opaque_pill_box else 3

    header = f"""[Script Info]
Title: SubForge Universal AI Subtitles ({target_lang})
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},58,&H00FFFFFF,&H0000FFFF,&H00101014,{back_colour},0,0,0,0,100,100,0,0,{border_style},{outline_px},2,2,80,80,64,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = []
    for cue in cues:
        txt = (cue.translated_text or cue.source_text).strip()
        if not txt:
            continue
        ass_txt = txt.replace("\n", "\\N")
        speaker = (cue.speaker or "").replace(",", " ")
        events.append(
            f"Dialogue: 0,{ms_to_ass_timestamp(cue.start_ms)},{ms_to_ass_timestamp(cue.end_ms)},"
            f"Default,{speaker},0,0,0,,{ass_txt}"
        )
    return header + "\n".join(events) + "\n"
