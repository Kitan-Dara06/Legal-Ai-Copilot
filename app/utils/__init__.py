import logging

logger = logging.getLogger(__name__)


def generate_final_answer(question: str, context_chunks: list[str], groq_client):
    """
    Takes the user's question and the list of LABELED chunks from the DB,
    and produces a cited legal response.
    """

    context_text = "\n\n" + "=" * 30 + "\n\n".join(context_chunks) + "\n\n" + "=" * 30

    system_prompt = f"""
    You are a professional Legal Analyst. Your task is to provide a grounded,
    precise answer to a user's question based strictly on the provided context.

    RULES FOR YOUR RESPONSE:
    1. USE ONLY PROVIDED CONTEXT: Do not use outside legal knowledge.
    2. MANDATORY CITATIONS: Every claim you make must be followed by a citation
       pointing to the Source and Page Number provided in the context header.
    3. CITATION FORMAT: Use parentheses, e.g., (Filename.pdf, Page X).
    4. NO HALLUCINATIONS: If the context does not contain the answer, explicitly state
       that the information is not available in the provided documents.
    5. STRUCTURE: Use bullet points or a table if the user asks for comparisons.

    6. COMPARISON MODE: If the user asks to compare documents or lists multiple contracts,
           you MUST output the answer as a Markdown Table.

           Table Format Example:
           | Contract Name | Clause Type | Snippet | Citation |
           |---------------|-------------|---------|----------|
           | Vendor Agrmt  | Termination | 30 days notice... | (vendor.pdf, Page 4) |
           | Lease Agrmt   | Breach      | Immediate...      | (lease.pdf, Page 9)  |

        CONTEXT:
        {context_text}
    """

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Based on the documents provided, {question}",
                },
            ],
            temperature=0,
            max_tokens=4096,
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error("Error generating final response: %s", e)
        return "I encountered an error while trying to synthesize the answer."


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
    return text[:2000]  # Cap length to prevent buffer overflow/context exhaustion


def is_scanned_pdf(file_bytes: bytes) -> bool:
    """Heuristic: if the first few pages contain almost no text, it's likely scanned."""
    import io

    # Try PyMuPDF (fitz) first as it is generally faster and already installed
    try:
        import fitz

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = ""
        for i in range(min(3, len(doc))):
            text += doc[i].get_text() or ""
        return (
            len(text.strip()) < 20
        )  # Digital PDFs have hundreds+ chars; scanned have near-zero.
    except Exception:
        pass

    # Fall back to pypdf
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        text = ""
        for i in range(min(3, len(reader.pages))):
            text += reader.pages[i].extract_text() or ""
        return len(text.strip()) < 20
    except Exception as e:
        logger.warning(f"Failed to check if PDF is scanned: {e}")
        return True  # Default to scanned (OCR) if we can't tell, to be safe
