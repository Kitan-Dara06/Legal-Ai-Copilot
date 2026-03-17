import logging
import os
from typing import Iterable, List

import httpx
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")
OPENROUTER_EMBEDDING_MODELS = [
    m.strip()
    for m in os.getenv(
        "OPENROUTER_EMBEDDING_MODELS",
        "baai/bge-m3,text-embedding-3-small",
    ).split(",")
    if m.strip()
]

# Optional OpenRouter attribution headers
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
OPENROUTER_X_TITLE = os.getenv("OPENROUTER_X_TITLE", "").strip()

# Cloudflare Workers AI (requested switch from OpenRouter)
CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_MODEL = "@cf/baai/bge-m3"

# Keep the existing SDK path for compatibility/perf when provider behaves well.
_sdk_client = OpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=OPENROUTER_API_KEY,
)


class EmbeddingProviderError(RuntimeError):
    """Raised for transient/provider-side embedding failures."""


def _prepare_texts(texts: Iterable[str]) -> List[str]:
    prepared: List[str] = []
    for t in texts:
        if not isinstance(t, str):
            continue
        cleaned = t.replace("\n", " ").strip()
        if cleaned:
            prepared.append(cleaned)
    return prepared


def _validate_embeddings(
    vectors: List[List[float]], expected_count: int, model: str
) -> List[List[float]]:
    if not vectors:
        raise EmbeddingProviderError(f"Model '{model}' returned zero embeddings")
    if len(vectors) != expected_count:
        raise EmbeddingProviderError(
            f"Model '{model}' returned {len(vectors)} embeddings for {expected_count} inputs"
        )

    dim = len(vectors[0])
    if dim == 0:
        raise EmbeddingProviderError(
            f"Model '{model}' returned empty embedding vectors"
        )

    for i, v in enumerate(vectors):
        if not isinstance(v, list) or not v:
            raise EmbeddingProviderError(
                f"Model '{model}' embedding[{i}] is empty/invalid"
            )
        if len(v) != dim:
            raise EmbeddingProviderError(
                f"Model '{model}' returned inconsistent dimensions: {len(v)} vs {dim}"
            )

    return vectors


def _extract_from_sdk_response(
    response, model: str, expected_count: int
) -> List[List[float]]:
    data = getattr(response, "data", None)
    if not data:
        raise EmbeddingProviderError(
            f"Model '{model}' returned no embedding data (SDK path)"
        )

    vectors: List[List[float]] = []
    for i, item in enumerate(data):
        emb = getattr(item, "embedding", None)
        if emb is None:
            raise EmbeddingProviderError(
                f"Model '{model}' missing embedding at index {i} (SDK path)"
            )
        vectors.append(emb)

    return _validate_embeddings(vectors, expected_count, model)


def _sdk_embeddings(model: str, inputs: List[str]) -> List[List[float]]:
    response = _sdk_client.embeddings.create(
        model=model,
        input=inputs,
    )
    return _extract_from_sdk_response(response, model, len(inputs))


def _http_embeddings(model: str, inputs: List[str]) -> List[List[float]]:
    if not OPENROUTER_API_KEY:
        raise EmbeddingProviderError("OPENROUTER_API_KEY is missing")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = OPENROUTER_HTTP_REFERER
    if OPENROUTER_X_TITLE:
        headers["X-Title"] = OPENROUTER_X_TITLE

    payload = {
        "model": model,
        "input": inputs,
    }

    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{OPENROUTER_BASE_URL}/embeddings", headers=headers, json=payload
        )

    if r.status_code >= 400:
        raise EmbeddingProviderError(
            f"Model '{model}' HTTP fallback failed with status {r.status_code}: {r.text[:300]}"
        )

    body = r.json()
    data = body.get("data")
    if not data:
        raise EmbeddingProviderError(
            f"Model '{model}' returned no embedding data (HTTP fallback)"
        )

    vectors: List[List[float]] = []
    for i, item in enumerate(data):
        emb = item.get("embedding")
        if emb is None:
            raise EmbeddingProviderError(
                f"Model '{model}' missing embedding at index {i} (HTTP fallback)"
            )
        vectors.append(emb)

    return _validate_embeddings(vectors, len(inputs), model)


def _cloudflare_embeddings(inputs: List[str]) -> List[List[float]]:
    """Fetches embeddings from Cloudflare Workers AI."""
    if not CLOUDFLARE_API_KEY or not CLOUDFLARE_ACCOUNT_ID:
        raise EmbeddingProviderError("Cloudflare credentials missing")

    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CLOUDFLARE_MODEL}"
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"text": inputs}

    with httpx.Client(timeout=60.0) as client:
        r = client.post(url, headers=headers, json=payload)

    if r.status_code >= 400:
        raise EmbeddingProviderError(
            f"Cloudflare API failed with status {r.status_code}: {r.text[:300]}"
        )

    body = r.json()
    if not body.get("success"):
        errors = body.get("errors", [])
        msg = errors[0].get("message") if errors else "Unknown Cloudflare error"
        raise EmbeddingProviderError(f"Cloudflare error: {msg}")

    result = body.get("result")
    if not result:
        raise EmbeddingProviderError("Cloudflare returned no result")

    vectors = result.get("data")
    if not vectors:
        raise EmbeddingProviderError("Cloudflare returned no embedding data")

    return _validate_embeddings(vectors, len(inputs), CLOUDFLARE_MODEL)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(EmbeddingProviderError),
    reraise=True,
)
def get_embedding(texts: list[str]) -> list[list[float]]:
    """
    Returns embeddings for provided texts.
    Prioritizes Cloudflare Workers AI if configured, falls back to OpenRouter.
    """
    inputs = _prepare_texts(texts)
    if not inputs:
        return []

    logger.info("Embedding %d text chunks", len(inputs))

    # Strategy 1: Cloudflare Workers AI (Preferred)
    if CLOUDFLARE_API_KEY and CLOUDFLARE_ACCOUNT_ID:
        try:
            return _cloudflare_embeddings(inputs)
        except Exception as cf_err:
            print(f"   ⚠️ Cloudflare Embedding failed: {cf_err}. Falling back...")

    # Strategy 2: OpenRouter (Fallback)
    models = OPENROUTER_EMBEDDING_MODELS or ["baai/bge-m3"]
    errors: List[str] = []

    for model in models:
        # Path A: SDK
        try:
            return _sdk_embeddings(model, inputs)
        except Exception as sdk_err:
            errors.append(f"{model} SDK: {type(sdk_err).__name__}: {sdk_err}")

        # Path B: raw HTTP fallback
        try:
            vectors = _http_embeddings(model, inputs)
            logger.info("Embedding HTTP fallback succeeded with model '%s'", model)
            return vectors
        except Exception as http_err:
            errors.append(f"{model} HTTP: {type(http_err).__name__}: {http_err}")

    message = " | ".join(errors[-6:])  # keep log size sane
    logger.error("All embedding APIs failed: %s", message)
    raise EmbeddingProviderError(message)
