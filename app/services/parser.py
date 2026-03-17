import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_from_pdf(file_file):
    reader = PdfReader(file_file)
    logger.info("Extracting text from %d PDF pages", len(reader.pages))
    documents = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            documents.append({"page": i + 1, "text": text})
    return documents
