import logging
import pypdf

logger = logging.getLogger(__name__)

def extract_from_pdf(file_file):
    """
    Extracts text from a digital PDF using pypdf.
    """
    documents = []
    reader = pypdf.PdfReader(file_file)
    logger.info("Extracting text from %d PDF pages using pypdf", len(reader.pages))
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            documents.append({"page": i + 1, "text": text.strip()})
    return documents
