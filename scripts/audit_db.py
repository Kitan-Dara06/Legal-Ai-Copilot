from app.services.store import engine

print("🔍 Auditing DB Metadata...")
data = engine.collection.get(limit=5, include=["metadatas", "documents"])

for i, meta in enumerate(data["metadatas"]):
    print(f"\n[{i}] Chunk: {data['documents'][i][:50]}...")
    print(f"    Parent ID: {meta.get('parent_id')}")
    print(f"    Context Len: {len(meta.get('section_context', ''))}")
    print(f"    Context Preview: {meta.get('section_context', '')[:100]}...")
