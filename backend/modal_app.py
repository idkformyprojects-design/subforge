"""
One-Command $0.00 Zero-Cost Serverless GPU Deployment on Modal.com!
Modal Starter Plan includes $30/month FREE GPU/CPU compute credits (no credit card required),
scales to $0.00 when idle, and runs ~300+ full 2-hour movies/month completely free.

Deploy in 60 seconds:
    pip install modal
    modal setup
    modal secret create subforge-secrets GEMINI_API_KEY=your_free_google_ai_studio_key
    modal deploy modal_app.py
"""

import os
import modal

app = modal.App("subforge-universal-ai-engine")

gpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install(
        "fastapi>=0.115.0",
        "python-multipart>=0.0.12",
        "pydantic>=2.9.0",
        "pydantic-settings>=2.6.0",
        "httpx>=0.27.2",
        "Pillow>=10.4.0",
        "faster-whisper>=1.1.0",
        "demucs>=4.0.1",
    )
    .env(
        {
            "PROVIDER_PROFILE": "FREE_ZERO_COST",
            "ENABLE_LOCAL_DEMUCS": "true",
            "ENABLE_LOCAL_WHISPERX": "true",
            "GEMINI_FREE_TIER_PACING": "true",
        }
    )
    .add_local_python_source("app")
)


@app.function(
    image=gpu_image,
    gpu="L4",  # ~$0.000222/sec burst only while processing; $0.00/sec when idle!
    timeout=900,
    scaledown_window=60,
    secrets=[modal.Secret.from_name("subforge-secrets")],
)
@modal.asgi_app()
def fastapi_endpoint():
    from app.main import app as subforge_fastapi_app

    return subforge_fastapi_app
