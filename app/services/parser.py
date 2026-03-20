import logging
import pdfplumber

logger = logging.getLogger(__name__)

def extract_from_pdf(file_file):
    """
    Extracts text from a digital PDF using pdfplumber for higher fidelity
    layout preservation (e.g., maintaining table structures and columns).
    """
    documents = []
    with pdfplumber.open(file_file) as pdf:
        logger.info("Extracting text from %d PDF pages using pdfplumber", len(pdf.pages))
        for i, page in enumerate(pdf.pages):
            text = page.extract_text(layout=True)
            if text and text.strip():
                documents.append({"page": i + 1, "text": text.strip()})
    return documents
