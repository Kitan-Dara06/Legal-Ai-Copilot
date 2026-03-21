# app/tasks_map_reduce.py
import os
import uuid
import logging
from typing import List, Dict, Optional
import pypdf
import io

from celery import chord, group
from app.worker import celery_app
from app.services.object_storage import download_file_from_gcs, delete_file_from_gcs
from app.services.parser import extract_from_pdf
from app.services.chunker import chunk_text
from app.services.embedder import get_embedding
from app.services.ocr_gemini import ocr_pdf_to_markdown_pages
from app.tasks import get_qdrant, ensure_collection_exists, update_progress_sync, update_postgres_status_sync, compute_sparse_vector
from qdrant_client.models import PointStruct

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

# ─────────────────────────────────────────────────────────────────────────────
# 1. DIGITAL PDF PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.map_reduce.dispatch_digital_pdf", bind=True)
def dispatch_digital_pdf(self, file_id: int, org_id: str, filename: str, blob_name: str):
    """
    Downloads digital PDF, extracts all text and chunks it quickly (CPU bound but fast),
    then fans out the slow network I/O (embeddings + Qdrant upsert) into parallel Map tasks.
    """
    local_temp_path = f"/tmp/{uuid.uuid4().hex}.pdf"
    
    try:
        update_progress_sync(file_id, 2)
        download_file_from_gcs(blob_name, local_temp_path)
        
        with open(local_temp_path, "rb") as f:
            pages_data = extract_from_pdf(f)

        if not pages_data:
            update_postgres_status_sync(file_id, "FAILED", error="No text could be extracted from PDF.")
            return

        full_text = "\n".join(p["text"] for p in pages_data)
        
        # Save full text to Postgres immediately
        update_postgres_status_sync(file_id, "PROCESSING", content=full_text)
        update_progress_sync(file_id, 10)

        chunk_objects = chunk_text(pages_data)
        total_chunks = len(chunk_objects)

        if total_chunks == 0:
            update_postgres_status_sync(file_id, "FAILED", error="Chunking produced no results.")
            return

        BATCH_SIZE = 100
        map_tasks = []
        
        for batch_start in range(0, total_chunks, BATCH_SIZE):
            batch = chunk_objects[batch_start : batch_start + BATCH_SIZE]
            # Strip out heavy data and pass only what's needed for embedding to keep Redis payload small
            map_tasks.append(
                embed_and_upsert_chunk_batch.s(file_id, org_id, filename, batch, batch_start)
            )

        # Execute parallel Maps, then the Reduce callback
        task_chord = chord(map_tasks)(finalize_pdf_processing.s(file_id, blob_name))
        
    except Exception as exc:
        update_postgres_status_sync(file_id, "FAILED", error=str(exc))
    finally:
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)

# ─────────────────────────────────────────────────────────────────────────────
# 2. SCANNED PDF PIPELINE (GEMINI OCR)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.map_reduce.dispatch_scanned_pdf", bind=True)
def dispatch_scanned_pdf(self, file_id: int, org_id: str, filename: str, blob_name: str):
    """
    Downloads a scanned PDF, counts the pages, and fans out 
    the extremely slow Gemini Vision OCR by chunks of pages.
    """
    local_temp_path = f"/tmp/{uuid.uuid4().hex}.pdf"
    
    try:
        update_progress_sync(file_id, 2)
        download_file_from_gcs(blob_name, local_temp_path)

        reader = pypdf.PdfReader(local_temp_path)
        total_pages = len(reader.pages)
        
        PAGE_BATCH_SIZE = 10 # OCR 10 pages per parallel worker
        map_tasks = []

        # Note: We must share the PDF blob_name with workers so they can download it
        # and slice out their assigned pages in-memory.
        for start_page in range(0, total_pages, PAGE_BATCH_SIZE):
            end_page = min(start_page + PAGE_BATCH_SIZE, total_pages)
            map_tasks.append(
                ocr_and_embed_scanned_batch.s(file_id, org_id, filename, blob_name, start_page, end_page)
            )

        task_chord = chord(map_tasks)(finalize_pdf_processing.s(file_id, blob_name))
        
    except Exception as exc:
        update_postgres_status_sync(file_id, "FAILED", error=str(exc))
    finally:
        if os.path.exists(local_temp_path):
            os.remove(local_temp_path)

# ─────────────────────────────────────────────────────────────────────────────
# 3. MAP TASKS (THE WORKERS)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.map_reduce.embed_and_upsert_chunk_batch", max_retries=3, default_retry_delay=10)
def embed_and_upsert_chunk_batch(file_id: int, org_id: str, filename: str, chunk_batch: List[Dict], global_start_idx: int):
    """
    MAP TASK (Digital): Takes pre-chunked text, calls embedding API, and upserts to Qdrant.
    Runs on N workers concurrently.
    """
    texts = [c["chunk_text"] for c in chunk_batch]
    vectors = get_embedding(texts)
    
    if not vectors:
        return {"status": "skipped", "reason": "No vectors returned from embedding API"}

    qdrant = get_qdrant()
    ensure_collection_exists(qdrant)
    batch_points = []
    
    for i, (chunk, vector) in enumerate(zip(chunk_batch, vectors)):
        global_idx = global_start_idx + i
        search_text = chunk.get("section_text", "") + " " + chunk["chunk_text"]
        sparse_vec = compute_sparse_vector(search_text)
        
        point_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{org_id}_{file_id}_{global_idx}"))
        
        batch_points.append(
            PointStruct(
                id=point_id,
                vector={"dense": vector, "text-sparse": sparse_vec},
                payload={
                    "file_id": file_id,
                    "org_id": org_id,
                    "chunk_text": chunk["chunk_text"],
                    "section_text": chunk.get("section_text", ""),
                    "parent_id": chunk.get("parent_id", ""),
                    "source_type": chunk.get("source_type", ""),
                    "page_number": chunk.get("page_number", 0),
                    "filename": filename,
                },
            )
        )
        
    if batch_points:
        qdrant.upsert(
            collection_name="legal_chunks",
            points=batch_points
        )
    return {"status": "success", "upserted": len(batch_points)}


@celery_app.task(name="app.tasks.map_reduce.ocr_and_embed_scanned_batch", queue="ocr", max_retries=3, default_retry_delay=30)
def ocr_and_embed_scanned_batch(file_id: int, org_id: str, filename: str, blob_name: str, start_page: int, end_page: int):
    """
    MAP TASK (Scanned): Downloads the PDF, crops exactly [start_page:end_page], 
    sends bytes to Gemini OCR, chunks the returned Markdown, embeds, and upserts.
    """
    local_temp = f"/tmp/{uuid.uuid4().hex}.pdf"
    try:
        download_file_from_gcs(blob_name, local_temp)
        
        # 1. In-Memory Slicing
        reader = pypdf.PdfReader(local_temp)
        writer = pypdf.PdfWriter()
        for i in range(start_page, end_page):
            writer.add_page(reader.pages[i])
            
        chunk_bytes = io.BytesIO()
        writer.write(chunk_bytes)
        pdf_bytes = chunk_bytes.getvalue()
        
        # 2. Gemini OCR
        pages_data = ocr_pdf_to_markdown_pages(pdf_bytes, page_count_hint=(end_page - start_page), api_key=GEMINI_API_KEY, model=GEMINI_MODEL)
        
        # Restore absolute page numbers for proper UI referencing
        for i, p in enumerate(pages_data):
            p["page"] = start_page + i + 1
            
        # 3. Chunking
        chunk_objects = chunk_text(pages_data)
        if not chunk_objects:
            return {"status": "skipped", "reason": "No text extracted"}
            
        # 4. Embedding + Upsert (we reuse the same sequence)
        embed_and_upsert_chunk_batch(file_id, org_id, filename, chunk_objects, global_start_idx=(start_page * 100)) # approximate index to avoid collisions
        return {"status": "success", "pages": (end_page - start_page)}
        
    finally:
        if os.path.exists(local_temp):
            os.remove(local_temp)


# ─────────────────────────────────────────────────────────────────────────────
# 4. REDUCE TASK (THE CLEANUP)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.map_reduce.finalize_pdf_processing")
def finalize_pdf_processing(results, file_id: int, blob_name: str):
    """
    REDUCE TASK: Fired via Celery Chord when all map children succeed.
    Marks Postgres DB as READY and cleans up Cloud Object Storage.
    """
    logger.info("All chunk batches finished for file_id %d. Results: %s", file_id, results)
    
    update_progress_sync(file_id, 100)
    update_postgres_status_sync(file_id, "READY")
    
    try:
        delete_file_from_gcs(blob_name)
    except Exception as e:
        logger.warning(f"Failed to delete original blob {blob_name}: {e}")
    
    return {"status": "completed", "file_id": file_id}
