# scripts/test_pipeline.py
import os
import sys
import uuid
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Add app to path
sys.path.append(os.getcwd())

from app.database import AsyncSessionLocal
from app.models import Workspace, Document, Organization, DefinedTermRegistry, DeadlineRegistry
from app.tasks import _process_document_core
from app.services.ingestion.graph_extractor import DependencyGraph

async def test_pipeline():
    print("🚀 Starting Pipeline Verification...")
    
    async with AsyncSessionLocal() as db:
        # 1. Get or create a test org
        res = await db.execute(select(Organization).limit(1))
        org = res.scalar_one_or_none()
        if not org:
            org = Organization(slug="test-org", name="Test Organization")
            db.add(org)
            await db.commit()
            await db.refresh(org)
        
        # 2. Create a test workspace
        workspace = Workspace(org_id=org.id, name="Test Pipeline Workspace")
        db.add(workspace)
        await db.commit()
        await db.refresh(workspace)
        
        # 3. Create a test document
        doc = Document(
            workspace_id=workspace.id,
            org_id=org.id,
            filename="Exhibit 10.pdf",
            status="PENDING",
            r2_key="test-key"
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        
        print(f"✅ Created Test Environment: Org={org.slug}, Workspace={workspace.id}, Doc={doc.id}")

        # 4. Run the core pipeline
        # (Using a real file from the pdfs directory)
        test_file_path = "./pdfs/Exhibit 10.pdf"
        if not os.path.exists(test_file_path):
            print(f"❌ Test file not found: {test_file_path}")
            return

        print("🔄 Running Pipeline (this will call LLMs and APIs)...")
        _process_document_core(
            str(doc.id),
            str(workspace.id),
            str(org.id),
            "Exhibit 10.pdf",
            test_file_path
        )

        # 5. Verify Results
        print("\n🔍 Verifying Results...")
        
        # A. Check Postgres (Terms)
        terms_res = await db.execute(select(DefinedTermRegistry).where(DefinedTermRegistry.source_document_id == doc.id))
        terms = terms_res.scalars().all()
        print(f"   - Defined Terms found: {len(terms)}")
        for t in terms[:3]:
            print(f"     * {t.term}")

        # B. Check Postgres (Deadlines)
        deadlines_res = await db.execute(select(DeadlineRegistry).where(DeadlineRegistry.workspace_id == workspace.id))
        deadlines = deadlines_res.scalars().all()
        print(f"   - Deadlines/Obligations found: {len(deadlines)}")
        for d in deadlines[:3]:
            print(f"     * {d.obligation_description}")

        # C. Check Neo4j
        graph = DependencyGraph()
        node_count = graph.number_of_nodes()
        edge_count = graph.number_of_edges()
        print(f"   - Neo4j Graph: {node_count} nodes, {edge_count} edges.")
        graph.close()

        # D. Check Document Status
        await db.refresh(doc)
        print(f"   - Document Status: {doc.status}")
        print(f"   - Intelligence Stages: {doc.intelligence_stages_complete}")

    print("\n🏁 Verification Complete!")

if __name__ == "__main__":
    asyncio.run(test_pipeline())
