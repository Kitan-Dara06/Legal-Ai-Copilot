import sys
from typing import Dict, List

from chunker import ClauseChunker

# Import the modules we just built
from parser import LegalDocumentParser
from reference_parser import LLMReferenceParser
from terms_extractor import DefinedTermExtractor


def run_pipeline(pdf_path: str) -> List[Dict]:
    print(f"\n--- Starting Unified Ingestion Pipeline for: {pdf_path} ---")

    # 1. Parse and Chunk
    print("\n[1/4] Extracting text and building hierarchical chunks...")
    doc_parser = LegalDocumentParser()
    raw_blocks = doc_parser.parse_pdf(pdf_path)
    chunker = ClauseChunker(body_font_size=doc_parser.body_font_size)
    chunks = chunker.build_chunks(raw_blocks)
    print(f"Generated {len(chunks)} base chunks.")

    # 2. Extract Defined Terms (Populates SQLite Registry)
    print("\n[2/4] Hunting for defined terms to build local registry...")
    term_extractor = DefinedTermExtractor()
    term_extractor.extract_and_store(chunks, document_name=pdf_path.split("/")[-1])

    # 3. Resolve Cross-References (NetworkX/FalkorDB Graph Prep)
    print("\n[3/4] Resolving internal cross-references via LLM...")
    ref_parser = LLMReferenceParser()
    enriched_chunks = ref_parser.resolve_reference(chunks)

    # 4. Final Output
    print("\n[4/4] Ingestion Complete. Chunks enriched and ready for vectorization.")
    return enriched_chunks


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python run_ingestion.py <path_to_pdf>")
        sys.exit(1)

    target_pdf = sys.argv[1]
    final_chunks = run_pipeline(target_pdf)

    # Print a sample to prove it worked
    if final_chunks:
        print("\n--- Sample Enriched Chunk ---")
        sample = final_chunks[0]
        print(f"Hierarchy Path: {' > '.join(sample['hierarchy'])}")
        print(f"Extracted Dependencies: {sample.get('dependency_clauses', [])}")
