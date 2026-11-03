from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def isolate_dialogue_vocals(input_audio_path: str, enable_demucs: bool = False) -> str:
    """
    Language-agnostic movie dialogue isolator:
    1. When running on Modal GPU (`enable_demucs=True` and `demucs` installed), runs Hybrid
       Transformer Demucs (`htdemucs --two-stems=vocals`) to cleanly strip background orchestral
       scores, songs, and explosions.
    2. Otherwise falls back to a studio-grade FFmpeg spectral voice bandpass + FFT denoiser +
       dynamic speech normalizer (`highpass=f=85,lowpass=f=7600,afftdn=nf=-25,speechnorm`)
       so speech recognition still gets clean vocals on any CPU host!
    """
    src = Path(input_audio_path)
    if not src.exists():
        raise FileNotFoundError(f"Input audio chunk not found: {input_audio_path}")

    out_dir = Path(tempfile.mkdtemp(prefix="subforge_vocals_"))

    if enable_demucs and shutil.which("demucs"):
        cmd = [
            "demucs",
            "-n",
            "htdemucs",
            "--two-stems=vocals",
            str(src),
            "-o",
            str(out_dir),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode == 0:
            candidate = out_dir / "htdemucs" / src.stem / "vocals.wav"
            if candidate.exists():
                opus_out = out_dir / f"{src.stem}_vocals.ogg"
                if _encode_clean_16k_opus(str(candidate), str(opus_out)):
                    return str(opus_out)
                return str(candidate)

    # Studio FFmpeg dialogue-isolation filter chain (fast, zero GPU required)
    if shutil.which("ffmpeg"):
        cleaned_out = out_dir / f"{src.stem}_dialogue.ogg"
        filter_chain = "highpass=f=85,lowpass=f=7600,afftdn=nf=-22,speechnorm=e=6:r=0.0001:l=1"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            filter_chain,
            "-c:a",
            "libopus",
            "-b:a",
            "24k",
            str(cleaned_out),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0 and cleaned_out.exists() and cleaned_out.stat().st_size > 128:
            return str(cleaned_out)

    return str(src)


def _encode_clean_16k_opus(in_wav: str, out_ogg: str) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        in_wav,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libopus",
        "-b:a",
        "24k",
        out_ogg,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return res.returncode == 0 and os.path.exists(out_ogg)
