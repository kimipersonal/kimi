"""Speech-to-Text service — multilingual voice transcription.

Supports two backends:
  1. Gemini multimodal (default) — uses Vertex AI credits, good quality
  2. Google Cloud Speech v2 / Chirp 2 (opt-in) — best accuracy, separate billing

Set STT_BACKEND=chirp2 in .env to use Chirp 2 instead of Gemini.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def transcribe_audio(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    language_hints: Optional[list[str]] = None,
) -> str:
    """Transcribe audio bytes to text.

    Uses Gemini (Vertex AI) by default. Set STT_BACKEND=chirp2 in config
    to use Google Cloud Speech-to-Text v2 (Chirp 2) for better accuracy
    (billed separately from Vertex AI credits).

    Args:
        audio_bytes: Raw audio file bytes.
        mime_type: MIME type of the audio (e.g. audio/ogg, audio/mpeg).
        language_hints: BCP-47 language codes to hint at. Defaults to uz/ru/en.

    Returns:
        Transcribed text string, or empty string on failure.
    """
    if language_hints is None:
        language_hints = ["uz-UZ", "ru-RU", "en-US"]

    from app.config import get_settings
    backend = get_settings().stt_backend

    if backend == "chirp2":
        # Chirp 2 (paid, separate from Vertex AI credits)
        try:
            return await _transcribe_chirp2(audio_bytes, language_hints)
        except Exception as e:
            logger.warning(f"Chirp 2 transcription failed, falling back to Gemini: {e}")

    # Default: Gemini multimodal (uses Vertex AI free credits)
    try:
        return await _transcribe_gemini(audio_bytes, mime_type, language_hints)
    except Exception as e:
        logger.error(f"Gemini transcription failed: {e}")

    # Last resort: try Chirp 2 if Gemini was the primary and failed
    if backend != "chirp2":
        try:
            return await _transcribe_chirp2(audio_bytes, language_hints)
        except Exception as e:
            logger.error(f"Chirp 2 fallback also failed: {e}")

    return ""


async def _transcribe_chirp2(
    audio_bytes: bytes,
    language_codes: list[str],
) -> str:
    """Transcribe using Google Cloud Speech-to-Text v2 with Chirp 2 model."""
    import asyncio
    from google.cloud.speech_v2 import SpeechClient
    from google.cloud.speech_v2.types import cloud_speech
    from app.config import get_settings

    settings = get_settings()
    project_id = settings.gcp_project_id
    if not project_id:
        raise RuntimeError("gcp_project_id not configured")

    def _sync_transcribe() -> str:
        client = SpeechClient()

        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=language_codes,
            model="chirp_2",
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
            ),
        )

        request = cloud_speech.RecognizeRequest(
            recognizer=f"projects/{project_id}/locations/global/recognizers/_",
            config=config,
            content=audio_bytes,
        )

        response = client.recognize(request=request)

        parts = []
        for result in response.results:
            if result.alternatives:
                parts.append(result.alternatives[0].transcript)

        return " ".join(parts).strip()

    # Run the synchronous gRPC call in a thread pool
    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(None, _sync_transcribe)

    if not transcript:
        raise RuntimeError("Chirp 2 returned empty transcription")

    logger.info(f"Chirp 2 transcription: {len(transcript)} chars, langs={language_codes}")
    return transcript


async def _transcribe_gemini(
    audio_bytes: bytes,
    mime_type: str,
    language_codes: list[str],
) -> str:
    """Fallback: transcribe using Gemini multimodal audio understanding."""
    import base64
    from app.services.llm_router import chat as llm_chat

    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

    # Build language hint for the prompt
    lang_names = {
        "uz-UZ": "Uzbek", "ru-RU": "Russian", "en-US": "English",
        "tr-TR": "Turkish", "kk-KZ": "Kazakh",
    }
    lang_str = ", ".join(lang_names.get(lc, lc) for lc in language_codes)

    response = await llm_chat(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Transcribe this voice message exactly as spoken. "
                            f"The speaker may use {lang_str}, or a mix of these languages. "
                            f"Preserve the original language — do NOT translate. "
                            f"Return ONLY the transcription text, nothing else."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{audio_b64}",
                        },
                    },
                ],
            }
        ],
        tier="fast",
    )

    transcript = response["content"].strip()
    if not transcript:
        raise RuntimeError("Gemini returned empty transcription")

    logger.info(f"Gemini transcription fallback: {len(transcript)} chars")
    return transcript
