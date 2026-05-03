import re
from typing import Dict, List


class ClauseChunker:
    """Consumes raw text blocks and builds a semantic legal hierarchy."""

    def __init__(self, body_font_size: float = 12.0):
        self.patterns = {
            "major_section": re.compile(
                r"^(?:ARTICLE|SECTION)\s+\d+(?:\.\d+)*\b", re.IGNORECASE
            ),
            "single_clause": re.compile(r"^(\d+)\.\s+(.*)"),
            "decimal_clause": re.compile(r"^(\d+\.\d+(?:\.\d+)*)\.?\s+(.*)"),
            "alpha_clause": re.compile(r"^\(([a-z])\)\s+(.*)"),
            "roman_clause": re.compile(r"^\(([ivx]+)\)\s+(.*)"),
        }
        self.body_font_size = body_font_size

    def _detect_clause_boundary(
        self, text: str, is_bold: bool, font_size: float
    ) -> str | None:
        """Extracts the exact identifier from the text using precise capture groups."""
        if is_bold and font_size > (self.body_font_size + 0.5) and len(text) < 150:
            return f"HEADER: {text[:30]}"

        # Using the walrus operator (:=) to cleanly check and extract
        if match := self.patterns["major_section"].match(text):
            return match.group(0).strip()  # e.g., "ARTICLE 1"

        if match := self.patterns["single_clause"].match(text):
            return f"{match.group(1)}."  # e.g., "1."

        if match := self.patterns["decimal_clause"].match(text):
            return match.group(1)  # e.g., "1.1" or "1.2.3"

        if match := self.patterns["alpha_clause"].match(text):
            return f"({match.group(1)})"  # e.g., "(a)"

        if match := self.patterns["roman_clause"].match(text):
            return f"({match.group(1)})"  # e.g., "(i)"

        return None

    def _get_node_level(self, node_str: str, current_hierarchy: list[str]) -> int:
        """Evaluates the level of an isolated identifier string."""

        # Level 0: HEADER: or ARTICLE/SECTION
        if node_str.startswith("HEADER:") or re.match(
            r"^(?:ARTICLE|SECTION)", node_str, re.IGNORECASE
        ):
            return 0

        # Level 1: "1." or "1.1" or "1.2.3" (Strictly numbers and dots)
        if re.match(r"^\d+(?:\.\d+)*\.?$", node_str):
            return 1

        # The Trap Check for Level 2 vs 3
        if node_str in ["(i)", "(v)", "(x)"]:
            for existing_node in reversed(current_hierarchy):
                if re.match(r"^\([a-z]\)$", existing_node):
                    if existing_node == "(h)" and node_str == "(i)":
                        return 2
                    if existing_node == "(u)" and node_str == "(v)":
                        return 2
                    if existing_node == "(w)" and node_str == "(x)":
                        return 2
                    break
            return 3

        # Level 3: Roman numerals (only checking i, v, x)
        if re.match(r"^\([ivx]+\)$", node_str):
            return 3

        # Level 2: Alpha characters
        if re.match(r"^\([a-z]\)$", node_str):
            return 2

        return 4

    def _update_hierarchy(self, current_hierarchy: list, new_node: str) -> list:
        new_level = self._get_node_level(new_node, current_hierarchy)
        updated_hierarchy = []

        for existing_node in current_hierarchy:
            if self._get_node_level(existing_node, current_hierarchy) < new_level:
                updated_hierarchy.append(existing_node)
            else:
                break

        updated_hierarchy.append(new_node)
        return updated_hierarchy  # FIXED: Return the trimmed list

    def build_chunks(self, raw_blocks: List[Dict]) -> List[Dict]:
        current_hierarchy = []
        current_chunk_text = []
        chunks = []

        for block in raw_blocks:
            node_id = self._detect_clause_boundary(
                block["text"], block["is_bold"], block["font_size"]
            )

            if node_id:
                if current_chunk_text:
                    chunks.append(
                        {
                            "hierarchy": list(current_hierarchy),
                            "text": " ".join(current_chunk_text),
                        }
                    )
                    current_chunk_text = []

                current_hierarchy = self._update_hierarchy(current_hierarchy, node_id)

            current_chunk_text.append(block["text"])

        if current_chunk_text:
            chunks.append(
                {
                    "hierarchy": list(current_hierarchy),
                    "text": " ".join(current_chunk_text),
                }
            )

        return chunks
