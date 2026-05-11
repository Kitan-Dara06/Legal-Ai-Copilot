import os
import sys
import json
import asyncio
from dotenv import load_dotenv
from groq import Groq

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

async def generate_pairs():
    # Load some contract context
    contract_path = "data/Exhibit 10.pdf"
    if not os.path.exists(contract_path):
         contract_path = "rageval_data/rageval_contract_Employment_Agreement_1.txt"
    
    with open(contract_path, "r") as f:
        # If it's pure text, just read it. If it was PDF, we should have used extract_from_pdf
        # but for simplicity let's assume we can get enough text.
        context = f.read()[:8000]

    prompt = f"""
    Generate 15 pairs of "Legal Semantic Inversions".
    Each pair consists of a query and two response chunks (A and B).
    One chunk should be the CORRECT answer from the contract, and the other should be a MINIMAL semantic inversion (legally opposite but semantically similar).
    
    EXAMPLE:
    Topic: Liability Cap
    Query: "Is there a limit on the Employer's liability?"
    Correct: "The Employer's liability is capped at $5,000,000 under Section 4."
    Inverted: "The Employer's liability is NOT capped at any amount under Section 4."
    
    EXAMPLE 2:
    Topic: Governing Law
    Query: "Which state laws govern this agreement?"
    Correct: "This Agreement shall be governed by the laws of the State of Ohio."
    Inverted: "This Agreement shall NOT be governed by the laws of the State of Ohio."
    
    CONTRACT TEXT:
    {context}
    
    OUTPUT FORMAT (JSON list of objects):
    [
      {{"topic": "...", "query": "...", "correct": "...", "inverted": "..."}},
      ...
    ]
    """
    
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"}
        )
        data = json.loads(response.choices[0].message.content)
        if isinstance(data, dict):
            pairs = data.get("pairs") or data.get("inversions") or next(iter(data.values()))
        else:
            pairs = data
            
        with open("reranker_test_set.json", "w") as f:
            json.dump(pairs, f, indent=2)
        print(f"Generated {len(pairs)} inversion pairs.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(generate_pairs())
