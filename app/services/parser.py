from pypdf import PdfReader


def extract_from_pdf(file_file):
    reader = PdfReader(file_file)
    print(f"Total pages: {len(reader.pages)}")
    documents = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            documents.append({"page": i + 1, "text": text})
    return documents
