import asyncio
import time
from tabulate import tabulate
from app.services.legal_primitives import search_tool

# Test Queries designed to stress-test the system
QUERIES = [
    # 1. Exact Reference (Hybrid/BM25 shoud win)
    "What does Section 14.2 say about termination?",
    
    # 2. Conceptual/Vocabulary Mismatch (Concept Expansion should win)
    "What happens if the company goes bankrupt?", 
    # (Contract might use "Insolvency", "Receivership", "Assignment for creditors")

    # 3. Phrasing Ambiguity (Multi-Query should win)
    "Can I fire the worker?",
    # (Contract might use "Termination of Employment", "Severance", "Just Cause")
]

def run_evaluation():
    print("\n⚖️ STARTING SEARCH STRATEGY COMPARISON ⚖️")
    print("=" * 80)
    
    results_table = []

    for q in QUERIES:
        print(f"\n❓ QUERY: {q}")
        
        # 1. Hybrid Baseline
        start = time.time()
        hybrid_res = search_tool(q, mode="hybrid", top_k=5)
        hybrid_time = time.time() - start
        
        # 2. Concept Expansion
        start = time.time()
        concept_res = search_tool(q, mode="concept", top_k=5)
        concept_time = time.time() - start
        
        # 3. Multi-Query
        start = time.time()
        multiquery_res = search_tool(q, mode="multiquery", top_k=5)
        multiquery_time = time.time() - start
        
        # Compare Overlaps
        h_set = set(hybrid_res)
        c_set = set(concept_res)
        m_set = set(multiquery_res)
        
        # Unique to each
        unique_h = len(h_set - c_set - m_set)
        unique_c = len(c_set - h_set - m_set)
        unique_m = len(m_set - h_set - c_set)
        
        common_all = len(h_set.intersection(c_set).intersection(m_set))
        
        results_table.append([q[:40]+"...", f"{hybrid_time:.2f}s", f"{concept_time:.2f}s", f"{multiquery_time:.2f}s", unique_h, unique_c, unique_m, common_all])
        
        print(f"   ► Hybrid: {len(hybrid_res)} docs ({hybrid_time:.2f}s)")
        print(f"   ► Concept: {len(concept_res)} docs ({concept_time:.2f}s)")
        print(f"   ► Multi-Q: {len(multiquery_res)} docs ({multiquery_time:.2f}s)")

    print("\n\n📊 FINAL COMPARISON REPORT")
    print("=" * 80)
    headers = ["Query", "T(Hybrid)", "T(Concept)", "T(MultiQ)", "U(Hybrid)", "U(Concept)", "U(MultiQ)", "Common"]
    print(tabulate(results_table, headers=headers, tablefmt="grid"))
    print("\nLegend:")
    print("T(...) = Time taken")
    print("U(...) = Unique docs found only by this method")
    print("Common = Docs found by ALL 3 methods")

if __name__ == "__main__":
    run_evaluation()
