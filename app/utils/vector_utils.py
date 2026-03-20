import hashlib
import re

def compute_sparse_vector(text: str) -> dict:
    """
    Creates a basic Term Frequency sparse vector for Qdrant (which applies IDF).
    Returns a dictionary ready for qdrant_client SparseVector instantiation.
    """
    # Simple tokenization: lowercase, alpha-numeric
    tokens = re.findall(r"\w+", text.lower())
    freq = {}
    for t in tokens:
        # Stable hash token to a 32-bit integer for sparse index
        hex_digest = hashlib.md5(t.encode("utf-8")).hexdigest()
        idx = int(hex_digest, 16) % (2**31 - 1)
        freq[idx] = freq.get(idx, 0) + 1

    indices = []
    values = []
    for k, v in freq.items():
        indices.append(k)
        values.append(float(v))
        
    # Return a dictionary that can be passed to qdrant_client.models.SparseVector
    return {"indices": indices, "values": values}
