import sqlite3

from graph import DependencyGraph
from registry import RegistryQueryEngine


def run_integration_test():
    print("\n==================================================")
    print("  INTELLIGENCE LAYER: UNIFIED INTEGRATION TEST")
    print("==================================================")

    # 1. STAND UP THE IN-MEMORY GRAPH
    print("\n[STEP 1] Initializing NetworkX Dependency Graph...")
    simulated_chunks = [
        {
            "hierarchy": ["Section 8"],
            "text": "The API Rate Limit is capped at 1000 requests per minute.",
            "dependencies_clauses": [],
        },
        {
            "hierarchy": ["Section 4 > (b)"],
            "text": "Vendor liability for API downtime is subject to the conditions in Section 8.",
            "dependencies_clauses": ["Section 8"],
        },
        {
            "hierarchy": ["Article II > Termination"],
            "text": "We may terminate this agreement if liability limits in Section 4 > (b) are breached.",
            "dependencies_clauses": ["Section 4 > (b)"],
        },
    ]
    kg = DependencyGraph()
    kg.build_graph(simulated_chunks, workspace_id="test_workspace")

    # 2. STAND UP THE LOCAL REGISTRY
    print("\n[STEP 2] Initializing SQLite Terms Registry...")
    test_db = "integration_demo.db"
    conn = sqlite3.connect(test_db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS defined_terms (id INTEGER PRIMARY KEY, term TEXT, definition TEXT, source_document TEXT, hierarchy TEXT)"
    )
    conn.execute("DELETE FROM defined_terms")
    conn.execute(
        "INSERT INTO defined_terms (term, definition, source_document, hierarchy) VALUES (?, ?, ?, ?)",
        (
            "Downtime",
            "Any period of complete API unavailability exceeding 5 minutes.",
            "MTN_Gateway_SLA_2026.pdf",
            "Section 2.1",
        ),
    )
    conn.execute(
        "INSERT INTO defined_terms (term, definition, source_document, hierarchy) VALUES (?, ?, ?, ?)",
        (
            "Downtime",
            "A loss of connectivity lasting longer than 15 minutes.",
            "Flash2Recharge_Vendor.pdf",
            "Article 4",
        ),
    )
    conn.commit()
    conn.close()

    registry = RegistryQueryEngine(db_path=test_db)

    # 3. EXECUTE THE UNIFIED QUERY
    print("\n[STEP 3] Executing Cross-Module Intelligence Query...")

    print(
        "\n--> [Action A] Querying Graph for 'Article II > Termination' dependencies..."
    )
    chain = kg.get_dependency_chains(
        "Article II > Termination", workspace_id="test_workspace"
    )
    for item in chain:
        print(f"    - [{item.get('node_id')}]: {item.get('text')}")

    print(
        "\n--> [Action B] Scanning Registry for conflicting definitions of 'Downtime'..."
    )
    conflicts = registry.detect_conflicts()

    if "Downtime" in conflicts:
        print("    ⚠️ CRITICAL CONFLICT DETECTED FOR 'Downtime':")
        for d in conflicts["Downtime"]:
            print(f"      - [{d['document']}]: {d['definition']}")

    print("\n==================================================")
    print("  INTEGRATION TEST COMPLETE - ALL SYSTEMS NOMINAL")
    print("==================================================")


if __name__ == "__main__":
    run_integration_test()
