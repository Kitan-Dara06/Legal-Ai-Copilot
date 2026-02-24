# inspect_chunks.py
import chromadb

# 1. Connect to the DB
client = chromadb.PersistentClient(path="./legal_db")
collection = client.get_or_create_collection("legal_contracts")

# 2. Fetch the first 10 chunks (both text and metadata)
# We request 'metadatas' specifically to see the context field
data = collection.get(limit=10, include=["documents", "metadatas"])

print(f"📦 Total Chunks in DB: {len(collection.get()['ids'])}")
print("=" * 60)

# 3. Loop and Compare
for i, doc in enumerate(data["documents"]):
    meta = data["metadatas"][i]

    print(f"🆔 Chunk ID: {data['ids'][i]}")
    print(f"📄 stored_text (What Vector Search sees):")
    print(f"   '{doc}'")

    print(f"🧠 section_context (What we WANT the LLM to see):")
    # Check if context exists and is different
    context = meta.get("section_context", "N/A")
    print(f"   '{context}'")

    print("-" * 60)
