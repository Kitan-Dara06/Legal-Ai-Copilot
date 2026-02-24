import re
import uuid
from typing import Dict, List


class RecursiveChunker:
    """
    Standard recursive splitter that breaks text down by separators
    (\n\n, \n, ., space) until it fits within chunk_size.
    """

    def __init__(self, chunk_size=1000, overlap=100):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.separators = ["\n\n", "\n", ". ", " ", ""]

    def split_text(self, text: str) -> List[str]:
        final_chunks = []
        self._split_recursively(text, self.separators, final_chunks)
        return [c for c in final_chunks if c.strip()]

    def _split_recursively(
        self, text: str, separators: List[str], final_chunks: List[str]
    ):
        if len(text) <= self.chunk_size:
            final_chunks.append(text)
            return

        if not separators:
            # Fallback: Hard split by characters if no separators left
            for i in range(0, len(text), self.chunk_size - self.overlap):
                final_chunks.append(text[i : i + self.chunk_size])
            return

        separator = separators[0]
        next_separators = separators[1:]

        if separator not in text:
            self._split_recursively(text, next_separators, final_chunks)
            return

        splits = text.split(separator)
        current_chunk = ""

        for split in splits:
            if len(current_chunk) + len(split) + len(separator) <= self.chunk_size:
                current_chunk += split + separator
            else:
                if current_chunk:
                    final_chunks.append(current_chunk.strip())

                if len(split) > self.chunk_size:
                    self._split_recursively(split, next_separators, final_chunks)
                    current_chunk = ""
                else:
                    current_chunk = split + separator

        if current_chunk:
            final_chunks.append(current_chunk.strip())


class HierarchicalChunker:
    """
    Intelligent Chunker that attempts to preserve Legal Structure (Headers).
    Falls back to Sliding Window for messy OCR text.
    """

    def __init__(self, chunk_size=1000, overlap=100):
        self.base_chunker = RecursiveChunker(chunk_size, overlap)

        # IMPROVED REGEX:
        # 1. Matches "1. " or "1.1 " or "Section 1"
        # 2. Requires a Capital Letter [A-Z] after the number to avoid matching dates or money.
        #    Example Matches: "1. EMPLOYMENT", "2.3 Term", "Article IV"
        self.section_pattern = re.compile(
            r"(?=^\s*(?:Article|Chapter|Section)?\s*(?:\d+\.)+\d*\s+[A-Z])",
            re.MULTILINE,
        )

    def split_into_sections(self, text: str) -> List[str]:
        # Split by the header regex
        sections = re.split(self.section_pattern, text)
        return [s.strip() for s in sections if s.strip()]

    def _create_sliding_window_hierarchy(self, text: str, page_num: int) -> List[Dict]:
        """
        Fallback for Unstructured Text (No Headers).
        Creates 'Artificial Parents' (Large Windows) and 'Children' (Small Chunks).
        """
        results = []

        # 1. Create ARTIFICIAL PARENTS (Big Contexts)
        # Size: 2000 chars (~500 tokens). Large enough for context, small enough to fit in LLM.
        parent_chunker = RecursiveChunker(chunk_size=2000, overlap=200)
        parent_texts = parent_chunker.split_text(text)

        for parent_text in parent_texts:
            parent_id = str(uuid.uuid4())

            # 2. Create CHILDREN (Search Targets) from within this Parent
            # Size: 500 chars. Focused enough for specific Vector Search hits.
            child_chunker = RecursiveChunker(chunk_size=500, overlap=50)
            child_texts = child_chunker.split_text(parent_text)

            for child_text in child_texts:
                results.append(
                    {
                        "parent_id": parent_id,
                        "section_text": parent_text,  # <--- The Big Window (Context)
                        "chunk_text": child_text,  # <--- The Search Hit (Target)
                        "page_number": page_num,
                        "source_type": "sliding_window",  # Useful for debugging
                    }
                )

        return results

    def chunk_hierarchically(self, pages_data: List[Dict]) -> List[Dict]:
        results = []

        for page_obj in pages_data:
            page_num = page_obj["page"]
            text = page_obj.get("text", "")

            # Basic safety check
            if not isinstance(text, str) or not text.strip():
                continue

            # 1. Try to find logical sections (The "Golden Path")
            sections = self.split_into_sections(text)

            # If regex found headers (e.g. "1. EMPLOYMENT"), use Structured Mode
            if len(sections) > 1:
                for section in sections:
                    parent_id = str(uuid.uuid4())

                    # Parent Context = The Header + The Paragraph
                    # We truncate to 2000 chars to avoid massive sections breaking the LLM
                    context_window = section[:2000]

                    # Break section into smaller searchable chunks
                    child_chunks = self.base_chunker.split_text(section)

                    for chunk in child_chunks:
                        results.append(
                            {
                                "parent_id": parent_id,
                                "section_text": context_window,  # <--- Structured Context
                                "chunk_text": chunk,  # <--- Small Chunk
                                "page_number": page_num,
                                "source_type": "structured_header",
                            }
                        )

            # 2. If regex failed (Unstructured/Messy OCR), use Safety Net
            else:
                results.extend(self._create_sliding_window_hierarchy(text, page_num))

        return results


# --- Helper Function used by Router ---
def chunk_text(pages_data: List[Dict], chunk_size=1000, overlap=100) -> List[Dict]:
    """
    Main entry point. Takes a list of page dicts and returns a list of chunk dicts.
    """
    chunker = HierarchicalChunker(chunk_size, overlap)
    return chunker.chunk_hierarchically(pages_data)
