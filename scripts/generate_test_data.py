import os
import json
from typing import Dict, List
from dotenv import load_dotenv
from pypdf import PdfReader
from openai import OpenAI

load_dotenv()

# List of your 4 specific files
PDF_FILES = [
    "Exhibit 10.pdf",
    "sec.gov_Archives_edgar_data_819793_000089109218004221_e78842ex10u.htm.pdf",
    "sec.gov_Archives_edgar_data_1654672_000149315218000875_ex10-8.htm.pdf",
    "Form of Employment Agreement.pdf" # Adding the 4th
]

def get_pdf_text(path):
    try:
        reader = PdfReader(path)
        return "\n".join([p.extract_text() for p in reader.pages if p.extract_text()])
    except: return ""

def generate_comparative_data():
    # 1. Load all texts into memory (Dict for easy mapping)
    print("1. Loading 4 PDFs into memory...")
    file_map = {f: get_pdf_text(f) for f in PDF_FILES}
    
    # 2. Key Legal Pillars to compare
    sections_to_compare = ["Termination", "Severance", "Non-Compete", "Governing Law"]
    total_comparative_data = []
    
    llm = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    for section in sections_to_compare:
        print(f"-> Comparing: {section} pillar...")
        
        context_block = ""
        for filename, text in file_map.items():
            start_idx = text.lower().find(section.lower())
            snippet = text[start_idx:start_idx+2500] if start_idx != -1 else "Clause not found."
            context_block += f"\n--- DOCUMENT: {filename} ---\n{snippet}\n"

        prompt = f"""You are a senior legal auditor.
        Compare the '{section}' clauses across these documents.
        
        {context_block}

        Generate 5 complex COMPARATIVE questions and ground-truth answers.
        IMPORTANT: Your questions must be strictly answerable by the provided text. Do not ask for attributes that do not exist in the text (e.g., do not ask for dollar amounts or notice periods if they are not explicitly mentioned in the section).
        
        OUTPUT FORMAT: JSON List of Lists only, for example:
        [
          ["Question 1?", "Answer 1"],
          ["Question 2?", "Answer 2"]
        ]
        """
        
        try:
            response = llm.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            # Unwrap any markdown fences if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            content = content.strip()
            parsed = json.loads(content)
            # Handle both {"data": [...]} wrapper and bare list
            if isinstance(parsed, dict):
                parsed = next(iter(parsed.values()))
            total_comparative_data.extend(parsed)
        except Exception as e:
            print(f"Error in {section}: {e}")

    with open("synthetic_eval_data.json", "w") as f:
        json.dump(total_comparative_data, f, indent=2)
    print(f"✅ Saved {len(total_comparative_data)} comparative questions.")

if __name__ == "__main__":
    generate_comparative_data()