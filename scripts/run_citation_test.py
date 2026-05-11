import os
import sys
import pandas as pd
import re
import json
from glob import glob
from dotenv import load_dotenv
from groq import Groq
import pypdf

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

def extract_claims_and_citations(text: str):
    """
    Parses RAG answers for inline citations:
    - (Filename.pdf, Page X)
    - (id.) or (id., Page X)
    """
    # Split into sentences to associate claims with citations
    sentences = re.split(r'(?<=[.!?]) +', text)
    results = []
    last_file = None
    last_page = None

    for sentence in sentences:
        # Match (Filename.pdf, Page X)
        full_match = re.search(r'\(([^()]*?\.pdf),\s*Page\s*(\d+)\)', sentence)
        # Match (id., Page X) or (id.)
        id_match = re.search(r'\(id\.(?:,\s*Page\s*(\d+))?\)', sentence)
        
        if full_match:
            last_file = full_match.group(1)
            last_page = int(full_match.group(2))
            clean_sentence = sentence.replace(full_match.group(0), "").strip()
            results.append({
                "claim": clean_sentence,
                "filename": last_file,
                "page": last_page
            })
        elif id_match and last_file:
            if id_match.group(1):
                last_page = int(id_match.group(1))
            clean_sentence = sentence.replace(id_match.group(0), "").strip()
            results.append({
                "claim": clean_sentence,
                "filename": last_file,
                "page": last_page
            })
    return results

def get_page_text(pdf_path: str, page_num: int):
    try:
        if not os.path.exists(pdf_path):
            return None
        reader = pypdf.PdfReader(pdf_path)
        if page_num > len(reader.pages):
            return None
        return reader.pages[page_num - 1].extract_text()
    except Exception as e:
        print(f"Error reading {pdf_path}: {e}")
        return None

def verify_claim(claim: str, page_text: str):
    prompt = f"""
    You are a legal auditor verifying the accuracy of a RAG system's claims.
    
    CLAIM TO VERIFY:
    "{claim}"
    
    CITED PAGE TEXT:
    ---
    {page_text}
    ---
    
    TASK:
    Does the cited page text contain specific evidence that supports the claim above?
    - If the page text supports the facts or values in the claim, respond with "SUPPORTED".
    - If the page text discusses the general topic but does NOT contain the specific fact or value, respond with "TOPIC_ONLY".
    - If the page text contradicts the claim, respond with "CONTRADICTED".
    - If the page text is completely unrelated, respond with "UNRELATED".
    
    Respond ONLY with one of the four words: SUPPORTED, TOPIC_ONLY, CONTRADICTED, UNRELATED.
    """
    
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=10
        )
        return response.choices[0].message.content.strip().upper()
    except Exception as e:
        print(f"Groq Error: {e}")
        return "ERROR"

def main():
    results_file = "parametric_test_results.csv"
    if not os.path.exists(results_file):
        print(f"❌ {results_file} not found. Run Experiment 1 first.")
        return

    df = pd.read_csv(results_file)
    # Filter out errors and empty answers
    df = df[~df['rag_answer'].str.contains("Error:", na=False)]
    df = df[df['rag_answer'].str.len() > 100]

    print(f"Starting Citation Audit on {len(df)} answers...")

    verification_records = []
    total_claims = 0
    supported_claims = 0

    for idx, row in df.iterrows():
        question = row['question']
        rag_answer = row['rag_answer']
        
        claims = extract_claims_and_citations(rag_answer)
        if not claims:
            continue
            
        print(f"\nEvaluating Question: {question[:50]}...")
        
        for j, c in enumerate(claims):
            total_claims += 1
            filename = c['filename']
            page = c['page']
            claim_text = c['claim']
            
            # Find the PDF file in the pdfs/ folder
            pdf_path = os.path.join("pdfs", filename)
            page_content = get_page_text(pdf_path, page)
            
            if page_content is None:
                print(f"  [Claim {j+1}] ❌ PDF or Page Not Found: {filename}, P{page}")
                verification_records.append({
                    "question": question,
                    "claim": claim_text,
                    "citation": f"{filename}, P{page}",
                    "result": "MISSING_SOURCE"
                })
                continue
            
            result = verify_claim(claim_text, page_content)
            print(f"  [Claim {j+1}] -> {result}")
            
            if result == "SUPPORTED":
                supported_claims += 1
                
            verification_records.append({
                "question": question,
                "claim": claim_text,
                "citation": f"{filename}, P{page}",
                "result": result
            })

    output_df = pd.DataFrame(verification_records)
    output_df.to_csv("citation_audit_results.csv", index=False)

    precision = (supported_claims / total_claims) * 100 if total_claims > 0 else 0
    print("\n" + "="*50)
    print("CITATION AUDIT COMPLETE")
    print(f"Total Claims Analyzed: {total_claims}")
    print(f"Supported Claims: {supported_claims}")
    print(f"Claim-level Citation Precision: {precision:.2f}%")
    
    if precision >= 85:
        print("✅ VERDICT: HIGH CITATION FIDELITY")
    elif precision >= 70:
        print("⚠️ VERDICT: MODERATE CITATION DRIFT")
    else:
        print("❌ VERDICT: COSMETIC CITATIONS DETECTED")
    print("="*50)

if __name__ == "__main__":
    main()
