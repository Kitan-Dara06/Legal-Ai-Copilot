#!/usr/bin/env python3
"""
Manual File Processing Test
Triggers a Celery task to process a test PDF and monitors Qdrant for chunk insertion.
"""

import os
import sys
import time
from io import BytesIO

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.store import get_global_qdrant
from app.worker import celery_app


def print_header(text):
    print(f"\n{'=' * 80}")
    print(f"  {text}")
    print(f"{'=' * 80}\n")


def print_success(text):
    print(f"✅ {text}")


def print_error(text):
    print(f"❌ {text}")


def print_info(text):
    print(f"ℹ️  {text}")


def create_test_pdf():
    """Create a minimal valid test PDF."""
    print_header("1. Creating Test PDF")

    # Minimal valid PDF with text content
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Resources 4 0 R /MediaBox [0 0 612 792] /Contents 5 0 R >>
endobj
4 0 obj
<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>
endobj
5 0 obj
<< /Length 180 >>
stream
BT
/F1 12 Tf
50 750 Td
(TEST CONTRACT - EMPLOYMENT AGREEMENT) Tj
0 -20 Td
(Section 1: Terms of Employment) Tj
0 -20 Td
(Employee shall work full-time starting January 1, 2025.) Tj
0 -20 Td
(Section 2: Compensation) Tj
0 -20 Td
(Base salary shall be $100,000 per year.) Tj
0 -20 Td
(Section 3: Termination) Tj
0 -20 Td
(Either party may terminate with 30 days notice.) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
0000000309 00000 n
trailer
<< /Size 6 /Root 1 0 R >>
startxref
539
%%EOF
"""

    test_path = "/tmp/test_contract.pdf"
    with open(test_path, "wb") as f:
        f.write(pdf_content)

    print_success(f"Test PDF created: {test_path}")
    return test_path


def check_qdrant_before():
    """Check Qdrant state before processing."""
    print_header("2. Qdrant State Before Processing")

    try:
        qdrant = get_global_qdrant()
        count = qdrant.count(collection_name="legal_chunks")
        print_info(f"Current chunk count: {count.count}")

        # Get existing file IDs
        records, _ = qdrant.scroll(
            collection_name="legal_chunks",
            limit=100,
            with_payload=["file_id", "filename"],
            with_vectors=False,
        )

        file_ids = set()
        for r in records:
            if r.payload:
                fid = r.payload.get("file_id")
                if fid:
                    file_ids.add(fid)

        if file_ids:
            print_info(f"Existing file IDs: {sorted(file_ids)}")
        else:
            print_info("No files currently indexed")

        return count.count

    except Exception as e:
        print_error(f"Failed to check Qdrant: {e}")
        return None


def trigger_processing_task(test_path):
    """Manually trigger the processing task."""
    print_header("3. Triggering Celery Task")

    # Simulate what the upload endpoint does
    test_file_id = 9999
    test_org_id = "test_org"
    test_filename = "test_contract.pdf"

    print_info(f"File ID: {test_file_id}")
    print_info(f"Org ID: {test_org_id}")
    print_info(f"Filename: {test_filename}")

    try:
        # Import the core processing function directly
        from app.tasks import _process_file_core

        print_info("Calling _process_file_core directly (synchronous)...")
        print_info("This will show all debug logs in real-time")

        _process_file_core(test_file_id, test_org_id, test_filename, test_path)

        print_success("Processing completed successfully!")
        return test_file_id

    except Exception as e:
        print_error(f"Processing failed: {e}")
        import traceback

        traceback.print_exc()
        return None


def check_qdrant_after(initial_count, file_id):
    """Check Qdrant state after processing."""
    print_header("4. Qdrant State After Processing")

    try:
        qdrant = get_global_qdrant()

        # Wait a moment for async operations to complete
        time.sleep(2)

        count = qdrant.count(collection_name="legal_chunks")
        new_count = count.count
        added = new_count - initial_count

        print_info(f"Previous count: {initial_count}")
        print_info(f"Current count: {new_count}")

        if added > 0:
            print_success(f"Added {added} new chunks!")
        else:
            print_error(f"No chunks added (delta: {added})")
            return False

        # Verify the specific file was indexed
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        records, _ = qdrant.scroll(
            collection_name="legal_chunks",
            scroll_filter=Filter(
                must=[FieldCondition(key="file_id", match=MatchValue(value=file_id))]
            ),
            limit=100,
            with_payload=True,
            with_vectors=False,
        )

        if records:
            print_success(f"Found {len(records)} chunks for file_id={file_id}")
            print_info("Sample chunk:")
            sample = records[0].payload
            print(f"  Filename: {sample.get('filename')}")
            print(f"  Page: {sample.get('page_number')}")
            print(f"  Chunk text preview: {sample.get('chunk_text', '')[:100]}...")
            return True
        else:
            print_error(f"No chunks found for file_id={file_id}")
            return False

    except Exception as e:
        print_error(f"Failed to check Qdrant: {e}")
        import traceback

        traceback.print_exc()
        return False


def cleanup(test_path, file_id):
    """Clean up test data."""
    print_header("5. Cleanup")

    # Remove test PDF
    try:
        if os.path.exists(test_path):
            os.remove(test_path)
            print_success(f"Removed test PDF: {test_path}")
    except Exception as e:
        print_error(f"Failed to remove test PDF: {e}")

    # Remove test chunks from Qdrant
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        qdrant = get_global_qdrant()
        qdrant.delete(
            collection_name="legal_chunks",
            points_selector=Filter(
                must=[FieldCondition(key="file_id", match=MatchValue(value=file_id))]
            ),
        )
        print_success(f"Removed test chunks for file_id={file_id} from Qdrant")
    except Exception as e:
        print_error(f"Failed to remove test chunks: {e}")


def main():
    print("\n" + "=" * 80)
    print("  FILE PROCESSING TEST")
    print("  Manually trigger processing and monitor Qdrant")
    print("=" * 80)

    # Step 1: Create test PDF
    test_path = create_test_pdf()

    # Step 2: Check Qdrant before
    initial_count = check_qdrant_before()
    if initial_count is None:
        print_error("Cannot proceed - Qdrant connection failed")
        return 1

    # Step 3: Trigger processing
    file_id = trigger_processing_task(test_path)
    if not file_id:
        print_error("Processing failed")
        cleanup(test_path, 9999)
        return 1

    # Step 4: Check Qdrant after
    success = check_qdrant_after(initial_count, file_id)

    # Step 5: Cleanup
    cleanup(test_path, file_id)

    # Summary
    print_header("SUMMARY")
    if success:
        print_success("File processing and Qdrant indexing working correctly!")
        return 0
    else:
        print_error("Qdrant indexing failed - chunks not inserted")
        print_info("Check the debug logs above for details")
        print_info("Common issues:")
        print_info("  1. Embedding API failures (OpenRouter)")
        print_info("  2. Qdrant connection/auth issues")
        print_info("  3. Exceptions in upsert logic")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
