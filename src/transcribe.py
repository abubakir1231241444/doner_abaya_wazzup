import base64
import logging
from openai import AsyncOpenAI
from src.config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

async def transcribe_audio(audio_bytes: bytes) -> str:
    """
    Переводит голосовое сообщение WhatsApp (OGG) в текст используя 
    OpenRouter API (модель google/gemini-2.5-flash с поддержкой аудио).
    """
    if not audio_bytes:
        return ""
        
    try:
        # OpenRouter audio input требует base64
        encoded = base64.b64encode(audio_bytes).decode("utf-8")
        
        client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            timeout=30.0,
        )
        
        response = await client.chat.completions.create(
            # Строго указываем Gemini, т.к. DeepSeek v3 не умеет слушать аудио
            model="google/gemini-2.5-flash",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text", 
                        "text": "Transcribe this audio precisely in its original language (Russian or Kazakh). Only output the transcription text, nothing else."
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": encoded,
                            "format": "ogg" # Wazzup обычно шлет голосовые в OGG (Opus)
                        }
                    }
                ]
            }],
            temperature=0.0
        )
        
        transcription = response.choices[0].message.content.strip()
        logger.info(f"Transcription success: {transcription}")
        return transcription
        
    except Exception as e:
        logger.error(f"Transcription API error: {e}")
        return ""
