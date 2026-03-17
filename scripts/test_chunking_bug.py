import os
import sys
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.parser import extract_from_pdf
from app.services.chunker import chunk_text

filename = "sec.gov_Archives_edgar_data_1654672_000149315218000875_ex10-8.htm.pdf"
filepath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename)

with open(filepath, "rb") as f:
    pages_data = extract_from_pdf(f)

print(f"Extracted {len(pages_data)} pages.")

# Search for Non-Compete in raw extracted text
found_in_raw = False
for p in pages_data:
    if "non-compete" in p["text"].lower() or "covenant period" in p["text"].lower():
        found_in_raw = True
        print(f"\n--- Found in RAW text on page {p['page']} ---")
        snippet = p["text"][p["text"].lower().find("non-compete")-50:p["text"].lower().find("non-compete")+50]
        print(f"Snippet: ...{snippet}...\n")

if not found_in_raw:
    print("❌ Not found in raw text. Issue is with PyPDF extraction!")
else:
    chunks = chunk_text(pages_data)
    print(f"Created {len(chunks)} chunks.")
    
    found_in_chunks = False
    for c in chunks:
        if "non-compete" in c["chunk_text"].lower() or "covenant period" in c["chunk_text"].lower():
            found_in_chunks = True
            print(f"✅ Found in chunk: {c['chunk_text'][:100]}")
    
    if not found_in_chunks:
        print("❌ Not found in chunks. Issue is with the chunking logic!")
