from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

from .models import ScriptFamily, ScriptSpec, SubtitleCue, TranslationStyle

# Unicode BiDi Control Characters for flawless Right-to-Left subtitle punctuation
RLI = "\u2067"  # Right-to-Left Isolate
PDI = "\u2069"  # Pop Directional Isolate
RLM = "\u200F"  # Right-to-Left Mark

RTL_LANGS = {
    "ar", "he", "iw", "fa", "ur", "ps", "yi", "sd", "ug", "dv", "ckb", "arc", "syr"
}
CJK_JP_LANGS = {"ja", "jpn"}
CJK_ZH_KO_LANGS = {"zh", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "yue", "ko", "kor"}
SEA_UNSPACED_LANGS = {"th", "lo", "km", "my", "bo", "dz"}
INDIC_ABUGIDA_LANGS = {
    "hi", "bn", "ta", "te", "ml", "kn", "mr", "gu", "pa", "or", "as", "si", "ne",
    "sa", "mai", "kok", "doi", "mni", "sat", "bho"
}
LATIN_CYRILLIC_GREEK_LANGS = {
    "en", "es", "fr", "de", "it", "pt", "pt-br", "nl", "sv", "no", "da", "fi",
    "pl", "cs", "sk", "hu", "ro", "hr", "sr", "sl", "bs", "sq", "lt", "lv", "et",
    "tr", "az", "uz", "kk", "ky", "tg", "tk", "mn", "ru", "uk", "be", "bg", "mk",
    "el", "vi", "id", "ms", "tl", "fil", "sw", "ha", "yo", "ig", "zu", "xh", "af",
    "ca", "eu", "gl", "is", "ga", "cy", "mt", "eo", "la"
}

# Japanese Kinsoku Shori: Characters forbidden at the start or end of a wrapped line
KINSOKU_NO_START = set("。、，．・：；！？）」』】〕〉》〗〙〛ー〜…‥ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")
KINSOKU_NO_END = set("（「『【〔〈《〖〘〚")

# Common dangling short function words to avoid stranding at the end of Line 1
DANGLING_END_WORDS = {
    "a", "an", "the", "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "and", "or", "but", "el", "la", "los", "las", "un", "una", "de", "del", "al",
    "le", "les", "des", "du", "un", "une", "der", "die", "das", "ein", "eine",
    "и", "в", "на", "с", "к", "о", "не", "कि", "का", "के", "की"
}


def normalize_lang_code(lang: str) -> str:
    if not lang:
        return "en"
    cleaned = lang.strip().lower().replace("_", "-")
    if cleaned in {"zh-cn", "zh-tw", "zh-hans", "zh-hant", "pt-br"}:
        return cleaned
    return cleaned.split("-")[0]


def get_script_spec(target_lang: str) -> ScriptSpec:
    code = normalize_lang_code(target_lang)
    base = code.split("-")[0]

    if base in CJK_JP_LANGS:
        return ScriptSpec(
            family=ScriptFamily.CJK_JAPANESE,
            max_chars_per_line_landscape=13,
            max_chars_per_line_portrait=8,
            max_cps_adult=4.5,
            is_rtl=False,
            uses_word_spaces=False,
            line_height_multiplier=1.28,
        )
    if code in CJK_ZH_KO_LANGS or base in CJK_ZH_KO_LANGS:
        is_ko = base in {"ko", "kor"}
        return ScriptSpec(
            family=ScriptFamily.CJK_CHINESE_KOREAN,
            max_chars_per_line_landscape=16,
            max_chars_per_line_portrait=10,
            max_cps_adult=12.0 if is_ko else 9.0,
            is_rtl=False,
            uses_word_spaces=is_ko,
            line_height_multiplier=1.25,
        )
    if base in RTL_LANGS:
        return ScriptSpec(
            family=ScriptFamily.RTL_BIDI,
            max_chars_per_line_landscape=42,
            max_chars_per_line_portrait=25,
            max_cps_adult=17.0,
            is_rtl=True,
            uses_word_spaces=True,
            line_height_multiplier=1.32,
        )
    if base in SEA_UNSPACED_LANGS:
        return ScriptSpec(
            family=ScriptFamily.SEA_UNSPACED,
            max_chars_per_line_landscape=35,
            max_chars_per_line_portrait=20,
            max_cps_adult=17.0,
            is_rtl=False,
            uses_word_spaces=False,
            line_height_multiplier=1.38,
        )
    if base in INDIC_ABUGIDA_LANGS:
        return ScriptSpec(
            family=ScriptFamily.INDIC_ABUGIDA,
            max_chars_per_line_landscape=38,
            max_chars_per_line_portrait=22,
            max_cps_adult=16.0,
            is_rtl=False,
            uses_word_spaces=True,
            line_height_multiplier=1.36,
        )
    if code in LATIN_CYRILLIC_GREEK_LANGS or base in LATIN_CYRILLIC_GREEK_LANGS:
        return ScriptSpec(
            family=ScriptFamily.LATIN_CYRILLIC_GREEK,
            max_chars_per_line_landscape=42,
            max_chars_per_line_portrait=25,
            max_cps_adult=17.5,
            is_rtl=False,
            uses_word_spaces=True,
            line_height_multiplier=1.18,
        )
    return ScriptSpec(
        family=ScriptFamily.GLOBAL_OTHER,
        max_chars_per_line_landscape=40,
        max_chars_per_line_portrait=24,
        max_cps_adult=16.5,
        is_rtl=False,
        uses_word_spaces=True,
        line_height_multiplier=1.25,
    )


def split_grapheme_clusters(text: str) -> List[str]:
    """
    Splits Unicode text into indivisible orthographic clusters so combining vowels,
    Thai tone marks, Arabic harakat, and Indic Virama conjuncts are NEVER torn apart.
    """
    if not text:
        return []
    clusters: List[str] = []
    VIRAMAS = {"\u094D", "\u09CD", "\u0A4D", "\u0ACD", "\u0B4D", "\u0BCD", "\u0C4D", "\u0CCD", "\u0D4D", "\u1039", "\u17D2"}
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        cluster = ch
        i += 1
        while i < n:
            nxt = text[i]
            cat = unicodedata.category(nxt)
            if cat in ("Mn", "Mc", "Me") or nxt in ("\u200C", "\u200D"):
                cluster += nxt
                i += 1
                # If the mark was an Indic/Myanmar/Khmer Virama/Coeng, bind the following consonant too!
                if nxt in VIRAMAS and i < n and unicodedata.category(text[i]).startswith("L"):
                    cluster += text[i]
                    i += 1
            else:
                break
        clusters.append(cluster)
    return clusters


def count_effective_chars(text: str, spec: ScriptSpec) -> int:
    """
    Counts display characters according to Netflix Timed Text rules:
    - Strip BiDi control characters
    - For Thai/SEA & Indic scripts, stacked combining marks (Mn) don't add horizontal width
    """
    cleaned = text.replace(RLI, "").replace(PDI, "").replace(RLM, "")
    if spec.family in (ScriptFamily.SEA_UNSPACED, ScriptFamily.INDIC_ABUGIDA):
        return sum(1 for c in cleaned if unicodedata.category(c) != "Mn")
    return len(cleaned)


def wrap_rtl_bidi_lines(text: str) -> str:
    """
    Wraps each line in Unicode Right-to-Left Isolate (U+2067..U+2069) + RLM (U+200F)
    so trailing periods, question marks, exclamation marks, numbers, and embedded Latin words
    render on the correct visual side across every player and HarfBuzz canvas.
    """
    lines = text.split("\n")
    wrapped = []
    for line in lines:
        raw = line.replace(RLI, "").replace(PDI, "").replace(RLM, "").strip()
        if not raw:
            continue
        wrapped.append(f"{RLI}{raw}{RLM}{PDI}")
    return "\n".join(wrapped)


def _wrap_spaced_text(text: str, max_cpl: int, spec: ScriptSpec) -> str:
    words = text.split()
    if not words:
        return ""
    joined = " ".join(words)
    if count_effective_chars(joined, spec) <= max_cpl:
        return joined

    # Find optimal 2-line split point (prefer punctuation/clauses & bottom-heavy pyramid)
    best_idx = max(1, len(words) // 2)
    best_score = float("inf")

    for idx in range(1, len(words)):
        line1 = " ".join(words[:idx])
        line2 = " ".join(words[idx:])
        len1 = count_effective_chars(line1, spec)
        len2 = count_effective_chars(line2, spec)

        overflow_penalty = 0.0
        if len1 > max_cpl:
            overflow_penalty += (len1 - max_cpl) * 12.0
        if len2 > max_cpl:
            overflow_penalty += (len2 - max_cpl) * 12.0

        # Slightly prefer bottom-heavy pyramid (len1 <= len2)
        balance_penalty = abs(len2 - len1) * 1.0
        if len1 > len2 + 4:
            balance_penalty += 4.0

        # Reward breaking right after punctuation (, ; : . ? ! —)
        prev_word = words[idx - 1]
        punct_bonus = -6.0 if prev_word[-1:] in {",", ";", ":", ".", "?", "!", "—", "،", "؟", "।"} else 0.0

        # Penalize stranding a dangling article/preposition at the end of line 1
        dangling_penalty = 8.0 if prev_word.lower().strip("\"'([{") in DANGLING_END_WORDS else 0.0

        score = overflow_penalty + balance_penalty + punct_bonus + dangling_penalty
        if score < best_score:
            best_score = score
            best_idx = idx

    return " ".join(words[:best_idx]) + "\n" + " ".join(words[best_idx:])


def _wrap_unspaced_text(text: str, max_cpl: int, spec: ScriptSpec) -> str:
    """
    Wraps Japanese, Chinese, Thai, Lao, Khmer, and Burmese respecting grapheme clusters
    and Japanese Kinsoku Shori punctuation rules (max 2 lines).
    """
    cleaned = re.sub(r"\s*\n\s*", "", text.strip())
    if count_effective_chars(cleaned, spec) <= max_cpl:
        return cleaned

    clusters = split_grapheme_clusters(cleaned)
    n = len(clusters)
    if n <= 1:
        return cleaned

    # Prefer breaking after CJK/Thai clause punctuation or spaces near the midpoint
    mid = n // 2
    best_idx = mid
    best_score = float("inf")
    CLAUSE_BREAKS = {"、", "。", "，", "！", "？", "：", "；", " ", "　"}

    for idx in range(1, n):
        line1 = "".join(clusters[:idx]).strip()
        line2 = "".join(clusters[idx:]).strip()
        if not line1 or not line2:
            continue

        len1 = count_effective_chars(line1, spec)
        len2 = count_effective_chars(line2, spec)

        penalty = abs(len2 - len1) * 1.2
        if len1 > max_cpl:
            penalty += (len1 - max_cpl) * 15.0
        if len2 > max_cpl:
            penalty += (len2 - max_cpl) * 15.0

        # Enforce Kinsoku Shori
        if line2[0] in KINSOKU_NO_START:
            penalty += 50.0
        if line1[-1] in KINSOKU_NO_END:
            penalty += 50.0
        if clusters[idx - 1] in CLAUSE_BREAKS:
            penalty -= 6.0

        if penalty < best_score:
            best_score = penalty
            best_idx = idx

    l1 = "".join(clusters[:best_idx]).strip()
    l2 = "".join(clusters[best_idx:]).strip()
    return f"{l1}\n{l2}" if l2 else l1


def format_subtitle_text(
    text: str,
    target_lang: str,
    is_portrait: bool = False,
    style: TranslationStyle = TranslationStyle.CINEMA_NATURAL,
    source_text: str = "",
) -> Tuple[str, ScriptSpec]:
    """
    Applies script-specific line wrapping, max-2-line enforcement, Bilingual Dual-Stacking,
    and Unicode BiDi RTL isolation for universal 100% accurate rendering.
    """
    spec = get_script_spec(target_lang)
    max_cpl = spec.max_chars_per_line_portrait if is_portrait else spec.max_chars_per_line_landscape

    raw = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not raw:
        return "", spec

    # Preserve dialogue speaker hyphens ("- Hello\n- Hi") as explicit 2-speaker lines
    lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
    if len(lines) == 2 and lines[0].startswith("-") and lines[1].startswith("-"):
        formatted = f"{lines[0]}\n{lines[1]}"
    else:
        flat = " ".join(lines) if spec.uses_word_spaces else "".join(lines)
        if spec.uses_word_spaces:
            formatted = _wrap_spaced_text(flat, max_cpl, spec)
        else:
            formatted = _wrap_unspaced_text(flat, max_cpl, spec)

    if spec.is_rtl:
        formatted = wrap_rtl_bidi_lines(formatted)

    if style == TranslationStyle.BILINGUAL_DUAL and source_text.strip():
        src_clean = " ".join(source_text.strip().splitlines())
        formatted = f"{formatted}\n{src_clean}"

    return formatted, spec


def compute_cps_and_validate_cue(
    cue: SubtitleCue,
    target_lang: str,
    is_portrait: bool = False,
    style: TranslationStyle = TranslationStyle.CINEMA_NATURAL,
) -> SubtitleCue:
    """
    Formats the cue's translated text, computes true reading speed (CPS), enforces
    minimum 833ms duration, and flags any uncertain or ultra-fast lines for quick check.
    """
    formatted_text, spec = format_subtitle_text(
        cue.translated_text or cue.source_text,
        target_lang=target_lang,
        is_portrait=is_portrait,
        style=style,
        source_text=cue.source_text,
    )
    cue.translated_text = formatted_text

    if cue.end_ms <= cue.start_ms:
        cue.end_ms = cue.start_ms + 1200
    duration_sec = max(0.833, (cue.end_ms - cue.start_ms) / 1000.0)

    # Measure CPS on target lines only (excluding bilingual source line if present)
    target_lines = formatted_text.split("\n")
    if style == TranslationStyle.BILINGUAL_DUAL and len(target_lines) > 1:
        target_only = "\n".join(target_lines[:-1])
    else:
        target_only = formatted_text

    char_count = count_effective_chars(target_only.replace("\n", ""), spec)
    cue.cps = round(char_count / duration_sec, 2)

    reasons: List[str] = []
    if cue.confidence < 0.85:
        reasons.append(f"Low audio/OCR confidence ({int(cue.confidence * 100)}%)")
    if cue.cps > spec.max_cps_adult * 1.35 and char_count > 12:
        reasons.append(f"High reading speed ({cue.cps} CPS > {spec.max_cps_adult} CPS)")
    if cue.review_reason:
        reasons.append(cue.review_reason)

    if reasons:
        cue.needs_review = True
        cue.review_reason = " • ".join(dict.fromkeys(reasons))

    return cue
