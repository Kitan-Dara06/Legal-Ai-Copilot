import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

CONTRACT_SCHEMAS = [
    {
        "type": "Employment Agreement",
        "focus": "Termination Notice and Severance",
        "variation": "Strict 90-day notice with 6 months severance, must mitigate damages."
    },
    {
        "type": "Master Services Agreement",
        "focus": "Intellectual Property Ownership",
        "variation": "Work-for-hire but contractor retains pre-existing tools and must license them."
    },
    {
        "type": "Commercial Lease",
        "focus": "Subletting and Assignment",
        "variation": "Landlord consent required, but cannot be unreasonably withheld if net worth > $10M."
    }
]

def generate_synthetic_contract(schema):
    prompt = f"""
    Generate a short, realistic 2-page legal contract section for a {schema['type']}.
    Focus specifically on {schema['focus']}.
    Specific variation to include: {schema['variation']}.
    
    Format:
    - Include Section headings (e.g., Section 1.1, Section 1.2).
    - Use formal legal language.
    - Ensure it is about 500-800 words long.
    
    Output ONLY the contract text.
    """
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content

def generate_qa_with_claims(contract_text, schema_type):
    prompt = f"""
    Based on the provided {schema_type} contract, generate 5 challenging questions that a user might ask.
    For each question, provide:
    1. The GROUND TRUTH answer.
    2. A list of ATOMIC CLAIMS that make up that answer.
    3. The exact QUOTE from the contract that supports each claim.
    
    CONTRACT TEXT:
    ---
    {contract_text}
    ---
    
    OUTPUT FORMAT (JSON):
    [
      {{
        "question": "...",
        "ground_truth": "...",
        "claims": [
          {{"claim": "...", "support_quote": "..."}},
          ...
        ]
      }},
      ...
    ]
    """
    
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

def main():
    all_data = []
    os.makedirs("rageval_data", exist_ok=True)
    
    print("Generating 30 synthetic test cases (6 sets of 5 questions)...")
    
    # We'll run 6 iterations of the 3 schemas to get 30 questions
    for i in range(2): # 2 iterations * 3 schemas * 5 questions = 30
        for schema in CONTRACT_SCHEMAS:
            print(f"Creating contract for: {schema['type']} (Iteration {i+1})...")
            contract = generate_synthetic_contract(schema)
            
            # Save contract to file for RAG ingestion
            contract_filename = f"rageval_contract_{schema['type'].replace(' ', '_')}_{i+1}.txt"
            contract_path = os.path.join("rageval_data", contract_filename)
            with open(contract_path, "w") as f:
                f.write(contract)
            
            print(f"Generating Q&A for {contract_filename}...")
            qa_pairs = generate_qa_with_claims(contract, schema['type'])
            
            for qa in qa_pairs.get('questions', qa_pairs.get('qa_pairs', qa_pairs.values() if isinstance(qa_pairs, dict) else qa_pairs)):
                if not isinstance(qa, dict): continue
                all_data.append({
                    "contract_file": contract_filename,
                    "question": qa['question'],
                    "ground_truth": qa['ground_truth'],
                    "claims": qa['claims']
                })

    with open("rageval_test_set.json", "w") as f:
        json.dump(all_data, f, indent=2)
    
    print(f"\n🎉 Generated {len(all_data)} synthetic test cases in rageval_test_set.json")
    print("Synthetic contracts saved in rageval_data/")

if __name__ == "__main__":
    main()
