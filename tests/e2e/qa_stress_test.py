import asyncio
import httpx
import os
import secrets
import time

BASE_URL = "http://127.0.0.1:8000"
NUM_CONCURRENT_UPLOADS = 15
FILE_SIZE_MB = 10

def generate_dummy_pdf(filename, size_mb):
    """Generates a dummy file of specified size."""
    size_bytes = size_mb * 1024 * 1024
    with open(filename, "wb") as f:
        # We just write random bytes to simulate a large abstract file payload
        f.write(secrets.token_bytes(size_bytes))
    print(f"Generated {filename} ({size_mb} MB)")

async def upload_file(client, file_path, endpoint):
    """Hits the specified endpoint with the file."""
    try:
        with open(file_path, "rb") as f:
            files = {"file" if "session" in endpoint else "files": (os.path.basename(file_path), f, "application/pdf")}
            url = f"{BASE_URL}{endpoint}"
            print(f"Uploading to {url}...")
            start_time = time.time()
            response = await client.post(url, files=files, timeout=60.0) # increased timeout for large files
            elapsed = time.time() - start_time
            print(f"✅ {url} - Status: {response.status_code} - Time: {elapsed:.2f}s")
            return response.status_code
    except Exception as e:
        print(f"❌ {endpoint} - Failed: {e}")
        return 500

async def perform_stress_test():
    print(f"🚀 Starting QA Stress Test: {NUM_CONCURRENT_UPLOADS} concurrent {FILE_SIZE_MB}MB files")
    
    # 1. Prepare dummy files
    dummy_files = []
    for i in range(NUM_CONCURRENT_UPLOADS):
        fname = f"qa_dummy_test_{i}.pdf"
        generate_dummy_pdf(fname, FILE_SIZE_MB)
        dummy_files.append(fname)
        
    async with httpx.AsyncClient() as client:
        # 2. Test Main Upload Endpoint
        print("\n--- 🧪 TEST 1: Stressing Main Injest Endpoint ---")
        tasks = []
        for i in range(NUM_CONCURRENT_UPLOADS):
            endpoint = f"/files/upload?org_id=qa_test_org_{i}"
            tasks.append(upload_file(client, dummy_files[i], endpoint))
            await asyncio.sleep(0.5) # Stagger to prevent instant Postgres DB lockups
            
        results = await asyncio.gather(*tasks)
        successful = sum(1 for r in results if r in (200, 202))
        print(f"Main Endpoint Result: {successful}/{NUM_CONCURRENT_UPLOADS} succeeded")

        # 3. Create a Session
        print("\n--- 🧪 TEST 2: Stressing Session Upload Endpoint ---")
        session_resp = await client.post(f"{BASE_URL}/session/?org_id=qa_test_org_session", json=[], timeout=60.0)
        if session_resp.status_code != 200:
            print(f"Failed to create session! {session_resp.text}")
        else:
            session_id = session_resp.json()["session_id"]
            print(f"Created QA test session: {session_id}")
            
            # 4. Stress Session Upload
            tasks = []
            for i in range(NUM_CONCURRENT_UPLOADS):
                endpoint = f"/session/{session_id}/upload?org_id=qa_test_org_session"
                tasks.append(upload_file(client, dummy_files[i], endpoint))
                await asyncio.sleep(0.5) # Stagger to prevent instant Postgres DB lockups
                
            results = await asyncio.gather(*tasks)
            successful = sum(1 for r in results if r in (200, 202))
            print(f"Session Endpoint Result: {successful}/{NUM_CONCURRENT_UPLOADS} succeeded")

    # 5. Cleanup
    print("\n🧹 Cleaning up generated dummy files...")
    for f in dummy_files:
        if os.path.exists(f):
            os.remove(f)

if __name__ == "__main__":
    asyncio.run(perform_stress_test())
    print("✅ Testing complete!")
