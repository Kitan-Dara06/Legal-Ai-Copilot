"""
Experimental chunkers — NOT used in production.
Kept here for reference and future experimentation.
"""
import time
import uuid
from typing import Dict, List


class LLMClauseBoundaryChunker:
    """
    Uses a lightweight LLM (Llama 3 8B) to identify precise logical clause boundaries.
    NOTE: Expensive (one API call per page). Use HierarchicalChunker for production.
    """
    def __init__(self):
        import os
        from groq import Groq
        self.client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        self.model = "llama-3.1-8b-instant"

    def split_into_clauses(self, text: str) -> List[str]:
        import json
        if not text.strip():
            return []
        prompt = f"""You are a legal document parser.
Analyze the following text and split it STRICTLY along autonomous legal clause boundaries.
Return a JSON object with a single key 'clauses' containing a list of strings.
TEXT:
{text}"""
        try:
            res = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}],
                temperature=0, response_format={"type": "json_object"},
            )
            data = json.loads(res.choices[0].message.content)
            return data.get("clauses", [text])
        except Exception as e:
            print(f"LLM Chunker Error: {e}")
            return [text]

    def chunk_hierarchically(self, pages_data: List[Dict]) -> List[Dict]:
        results = []
        for page_obj in pages_data:
            page_num = page_obj["page"]
            text = page_obj.get("text", "")
            if not text.strip():
                continue
            clauses = self.split_into_clauses(text)
            time.sleep(0.5)
            parent_id = str(uuid.uuid4())
            for chunk in clauses:
                results.append({
                    "parent_id": parent_id, "section_text": text[:2000],
                    "chunk_text": chunk, "page_number": page_num,
                    "source_type": "llm_clause",
                })
        return results


class LongUnitChunker:
    """
    Experiment 9: LongRAG Validation.
    Groups related clauses into massive chunks (~12000 chars) to preserve macro-context.
    NOTE: Degrades recall on specific clause questions. Best for broad summarisation.
    """
    from app.services.chunker import RecursiveChunker

    def __init__(self, chunk_size=12000, overlap=1200):
        from app.services.chunker import RecursiveChunker
        self.chunker = RecursiveChunker(chunk_size=chunk_size, overlap=overlap)

    def chunk_hierarchically(self, pages_data: List[Dict]) -> List[Dict]:
        full_text = "".join(p.get("text", "") + "\n\n" for p in pages_data)
        chunks = self.chunker.split_text(full_text)
        parent_id = str(uuid.uuid4())
        return [
            {
                "parent_id": parent_id, "section_text": "",
                "chunk_text": c, "page_number": pages_data[0]["page"] if pages_data else 0,
                "source_type": "longrag_unit",
            }
            for c in chunks if c.strip()
        ]
