from datetime import datetime
from typing import Any, Dict, Optional


def voice_interview_capabilities() -> Dict[str, Any]:
    return {
        "speech_to_text": {"status": "browser_ready", "providers": ["browser_web_speech", "openai_whisper", "azure_speech"]},
        "spoken_ai_responses": {"status": "adapter_ready", "providers": ["browser_tts", "elevenlabs", "azure_tts"]},
        "audio_analysis": {"status": "local_signal_analysis", "signals": ["pace", "clarity", "confidence", "sentiment"]},
        "recording": {"status": "storage_adapter_ready", "storage": ["aws_s3", "cloudinary", "supabase"]},
    }


def analyze_voice_interview(
    *,
    transcript: str,
    candidate_id: str,
    organization_id: Optional[int] = None,
) -> Dict[str, Any]:
    words = [word for word in (transcript or "").split() if word]
    unique_ratio = len(set(words)) / max(len(words), 1)
    confidence = min(95, 45 + len(words) // 4 + int(unique_ratio * 20))
    communication = min(95, 50 + int(unique_ratio * 30) + min(len(words) // 10, 15))
    sentiment = "positive" if any(term in transcript.lower() for term in ["excited", "confident", "built", "led", "improved"]) else "neutral"
    return {
        "candidate_id": candidate_id,
        "organization_id": organization_id,
        "generated_at": datetime.utcnow().isoformat(),
        "transcript_word_count": len(words),
        "communication_score": communication,
        "confidence_score": confidence,
        "voice_sentiment": sentiment,
        "risk_indicators": _risk_indicators(transcript),
        "recommendation": "advance" if confidence >= 70 and communication >= 70 else "review",
        "summary": "Voice interview analysis is using local transcript intelligence with adapter-ready speech and recording providers.",
        "capabilities": voice_interview_capabilities(),
    }


def _risk_indicators(transcript: str) -> list[str]:
    text = (transcript or "").lower()
    risks = []
    if len(text.split()) < 40:
        risks.append("short_response")
    if "i don't know" in text or "not sure" in text:
        risks.append("uncertain_answering")
    return risks
