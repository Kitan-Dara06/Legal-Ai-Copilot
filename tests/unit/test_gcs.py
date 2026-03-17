from google.cloud import storage

def test_connection():
    print("Testing Google Cloud Storage connection...")
    
    # Replace with your actual Project ID if it changed
    PROJECT_ID = "project-416b2dca-bc60-4711-9c3" 
    
    try:
        # Explicitly pass the project ID here
        client = storage.Client(project=PROJECT_ID)
        
        buckets = list(client.list_buckets())
        print(f"✅ Success! Connected to GCP. Found {len(buckets)} buckets.")
        
        for bucket in buckets:
            print(f" 🪣 - {bucket.name}")
            
    except Exception as e:
        print(f"❌ Connection failed: {e}")

if __name__ == "__main__":
    test_connection()