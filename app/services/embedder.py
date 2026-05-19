"""
Query-time embedder for Lex.

Primary:  Voyage AI (voyage-law-2, 1024-dim) — must match the indexed vector space.
Fallback: Cloudflare Workers AI → OpenRouter (bge-m3, 1024-dim) — availability only.

WARNING: Fallback providers use a different embedding space than the Qdrant index.
         Retrieval quality degrades when Voyage is unavailable. Log a warning.
"""

import logging
import os
from typing import List

import httpx
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
    before_sleep_log,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ── Voyage AI (Primary) ────────────────────────────────────────────────────────
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "").strip()
VOYAGE_MODEL = "voyage-law-2"

# ── Cloudflare Workers AI (Fallback 1) ─────────────────────────────────────────
CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_MODEL = "@cf/baai/bge-m3"

# ── OpenRouter (Fallback 2 / last resort) ──────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")
OPENROUTER_EMBEDDING_MODELS = [
    m.strip()
    for m in os.getenv("OPENROUTER_EMBEDDING_MODELS", "baai/bge-m3").split(",")
    if m.strip()
]

# Lazily initialised SDK clients — no connection at import time.
_voyage_client = None


class EmbeddingProviderError(RuntimeError):
    """Raised for transient/provider-side embedding failures."""


class VoyageRateLimitError(EmbeddingProviderError):
    """Raised specifically for Voyage HTTP 429 / 529 responses.

    Tenacity retries this class with longer backoff than generic errors,
    because Voyage rate limits reset in 30–60s windows.
    """


# ── Helpers ────────────────────────────────────────────────────────────────────


def _prepare_texts(texts) -> List[str]:
    prepared: List[str] = []
    for t in texts:
        if not isinstance(t, str):
            continue
        cleaned = t.replace("\n", " ").strip()
        if cleaned:
            prepared.append(cleaned)
    return prepared


def _get_voyage_client():
    global _voyage_client
    if _voyage_client is None:
        import voyageai
        _voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)
    return _voyage_client


# ── Provider implementations ───────────────────────────────────────────────────


def _voyage_embeddings(inputs: List[str]) -> List[List[float]]:
    if not VOYAGE_API_KEY:
        raise EmbeddingProviderError("VOYAGE_API_KEY is not set.")
    try:
        client = _get_voyage_client()
        result = client.embed(inputs, model=VOYAGE_MODEL, input_type="query")
        embeddings = result.embeddings
        if not embeddings or len(embeddings) != len(inputs):
            raise EmbeddingProviderError(
                f"Voyage returned {len(embeddings or [])} embeddings for {len(inputs)} inputs."
            )
        return embeddings
    except EmbeddingProviderError:
        raise
    except Exception as exc:
        exc_str = str(exc).lower()
        # Voyage uses HTTP 429 for rate limits and 529 for overload
        if "429" in exc_str or "rate limit" in exc_str or "529" in exc_str or "too many" in exc_str:
            raise VoyageRateLimitError(
                f"Voyage rate limited — will retry with backoff: {exc}"
            ) from exc
        raise EmbeddingProviderError(f"Voyage API error: {exc}") from exc


def _cloudflare_embeddings(inputs: List[str]) -> List[List[float]]:
    if not CLOUDFLARE_API_KEY or not CLOUDFLARE_ACCOUNT_ID:
        raise EmbeddingProviderError("Cloudflare credentials (API key / account ID) not set.")
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_MODEL}"
    )
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            url,
            headers={"Authorization": f"Bearer {CLOUDFLARE_API_KEY}"},
            json={"text": inputs},
        )
    if r.status_code >= 400:
        raise EmbeddingProviderError(
            f"Cloudflare returned HTTP {r.status_code}: {r.text[:200]}"
        )
    body = r.json()
    if not body.get("success"):
        errors = body.get("errors", [])
        msg = errors[0].get("message") if errors else "Unknown Cloudflare error"
        raise EmbeddingProviderError(f"Cloudflare error: {msg}")
    vectors = body.get("result", {}).get("data", [])
    if not vectors:
        raise EmbeddingProviderError("Cloudflare returned no embedding vectors.")
    return vectors


def _openrouter_embeddings(inputs: List[str]) -> List[List[float]]:
    if not OPENROUTER_API_KEY:
        raise EmbeddingProviderError("OPENROUTER_API_KEY is not set.")
    models = OPENROUTER_EMBEDDING_MODELS or ["baai/bge-m3"]
    last_error: Exception | None = None
    for model in models:
        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    f"{OPENROUTER_BASE_URL}/embeddings",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                    json={"model": model, "input": inputs},
                )
            if r.status_code >= 400:
                last_error = EmbeddingProviderError(
                    f"OpenRouter {model} HTTP {r.status_code}: {r.text[:200]}"
                )
                continue
            data = r.json().get("data", [])
            if data:
                return [item["embedding"] for item in data]
        except Exception as exc:
            last_error = exc
            continue
    raise EmbeddingProviderError(
        f"All OpenRouter models failed. Last error: {last_error}"
    )


# ── Public API ─────────────────────────────────────────────────────────────────


@retry(
    # Voyage rate limits: back off hard, up to 5 attempts (~2 min total)
    retry=retry_if_exception_type(VoyageRateLimitError),
    wait=wait_random_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=False,  # On exhaustion, fall through to next provider
)
@retry(
    # Transient errors: quick retry
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=15),
    retry=retry_if_exception_type(EmbeddingProviderError),
    reraise=True,
)
def get_embedding(texts: list[str]) -> list[list[float]]:
    """
    Returns query embeddings.

    Primary:    Voyage AI (voyage-law-2) — matches the index embedding space.
    Fallback 1: Cloudflare Workers AI (bge-m3) — different space, degraded quality.
    Fallback 2: OpenRouter (bge-m3) — last resort, degraded quality.
    """
    inputs = _prepare_texts(texts)
    if not inputs:
        return []

    logger.info("Generating query embedding for %d text(s) (voyage-law-2)", len(inputs))

    # 1. Voyage AI — correct embedding space (must succeed in normal operation)
    try:
        return _voyage_embeddings(inputs)
    except EmbeddingProviderError as exc:
        logger.warning(
            "Voyage AI embedding failed: %s — falling back to Cloudflare (quality degraded)",
            exc,
        )

    # 2. Cloudflare Workers AI — availability fallback
    try:
        vecs = _cloudflare_embeddings(inputs)
        logger.warning(
            "Using Cloudflare bge-m3 as embedding fallback — "
            "retrieval quality is degraded (different embedding space from index)."
        )
        return vecs
    except EmbeddingProviderError as exc:
        logger.warning("Cloudflare embedding failed: %s — trying OpenRouter", exc)

    # 3. OpenRouter — last resort
    logger.warning(
        "Using OpenRouter as last-resort embedding fallback — retrieval quality degraded."
    )
    return _openrouter_embeddings(inputs)
