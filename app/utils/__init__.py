import io
import logging

logger = logging.getLogger(__name__)


def sanitize_goal_text(text: str) -> str:
    """Basic prompt injection defense."""
    forbidden_phrases = [
        "ignore previous",
        "forget instructions",
        "system prompt",
        "output all",
        "bypass",
        "disregard",
        "print instructions",
    ]
    lower_text = text.lower()
    for phrase in forbidden_phrases:
        if phrase in lower_text:
            logger.warning("Prompt injection attempt detected and sanitized.")
            return "Provide a safe, default legal analysis of the documents."
    # Cap length to prevent context exhaustion.
    return text[:2000]


def is_scanned_pdf(file_bytes: bytes) -> bool:
    """Heuristic: if first pages have little text, treat as scanned."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for i in range(min(3, len(reader.pages))):
            text += reader.pages[i].extract_text() or ""
        return len(text.strip()) < 50
    except Exception as e:
        logger.warning("Failed to check if PDF is scanned: %s", e)
        # Fail safe toward OCR path.
        return True
