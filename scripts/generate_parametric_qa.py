import os
import json
import random
import glob
from dotenv import load_dotenv
import pypdf
import groq

# Load environment variables
load_dotenv()
load_dotenv(".env.production")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is not set. Please set it in your .env file.")

client = groq.Groq(api_key=GROQ_API_KEY)

PDF_DIR = "pdfs"
OUTPUT_FILE = "parametric_eval_data.json"
NUM_QUESTIONS = 50

def extract_chunks_from_pdfs(pdf_dir, chunk_size=2000):
    """Extracts text from all PDFs in the directory and splits them into chunks."""
    pdf_files = glob.glob(os.path.join(pdf_dir, "*.pdf"))
    if not pdf_files:
        raise ValueError(f"No PDF files found in {pdf_dir}")
        
    all_text = []
    
    for pdf_path in pdf_files:
        print(f"Reading {pdf_path}...")
        try:
            with open(pdf_path, 'rb') as f:
                reader = pypdf.PdfReader(f)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        all_text.append(text)
        except Exception as e:
            print(f"Error reading {pdf_path}: {e}")
            
    full_text = "\n".join(all_text)
    
    # Simple chunking
    chunks = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]
    print(f"Total chunks extracted: {len(chunks)}")
    return chunks

def generate_questions(chunks, num_questions):
    """Generates evaluation questions using Groq."""
    questions = []
    
    # We will generate 5 questions per prompt to save API calls
    questions_per_batch = 5
    num_batches = num_questions // questions_per_batch
    
    print(f"Generating {num_questions} questions in {num_batches} batches...")
    
    for i in range(num_batches):
        # Pick a random chunk for context
        chunk = random.choice(chunks)
        
        prompt = f"""
        You are a legal evaluation expert. Your task is to generate {questions_per_batch} realistic questions that a user might ask based on the following legal text context. 
        The questions should be specific enough to require the context to answer accurately, but framed as if the user is asking about their own legal documents.
        
        Examples of good questions: "What is the notice period required for termination?", "Who are the parties involved in the Second Lease Amendment?", "Is the employee subject to a non-compete clause?"
        
        Context:
        ---
        {chunk}
        ---
        
        Output ONLY a JSON array of strings containing the {questions_per_batch} questions, with no markdown formatting, no explanations, just the JSON array.
        Example output format:
        [
            "Question 1",
            "Question 2",
            "Question 3",
            "Question 4",
            "Question 5"
        ]
        """
        
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=1000,
            )
            
            # Parse the JSON response
            content = response.choices[0].message.content.strip()
            # Clean up potential markdown blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
                
            batch_questions = json.loads(content)
            if isinstance(batch_questions, list):
                questions.extend(batch_questions)
                print(f"Generated {len(questions)}/{num_questions} questions...")
            else:
                print(f"Warning: Expected array, got {type(batch_questions)}")
        except Exception as e:
            print(f"Error generating batch {i+1}: {e}")
            
    # If we need slightly more to hit exactly 50
    return questions[:num_questions]

def main():
    chunks = extract_chunks_from_pdfs(PDF_DIR)
    
    if not chunks:
        print("No chunks extracted. Exiting.")
        return
        
    questions = generate_questions(chunks, NUM_QUESTIONS)
    
    # Ensure format matches what the eval script expects
    # For evaluate_from_csv.py or synthetic_eval_data.json, format is usually a list of dicts: {"question": "...", "ground_truth": "..."}
    # Since we only test parametric vs contextual, we just need the questions. We will save as synthetic eval format just in case.
    formatted_data = [{"question": q, "ground_truth": ""} for q in questions]
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(formatted_data, f, indent=4)
        
    print(f"\nSuccessfully saved {len(formatted_data)} questions to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
