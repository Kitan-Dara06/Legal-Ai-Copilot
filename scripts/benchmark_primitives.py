import time
import json
from app.services.legal_primitives import read_tool, logic_tool
from pprint import pprint

# Using a known contract from the previous log trace
sample_contract = "Form of Non-Solicitation Agreement and Confidentiality Agreement.pdf"
print(f"✅ Using known contract: {sample_contract}")

def benchmark_read_tool():
    print("\n" + "="*50)
    print("⏱️ BENCHMARK: read_tool (Extraction)")
    print("="*50)
    target_fields = ["effective_date", "parties", "governing_law"]
    
    start_time = time.time()
    try:
        # read_tool natively takes the contract name, does a hybrid search, and extracts the fields
        result = read_tool(contract_name=sample_contract, target_fields=target_fields)
        end_time = time.time()
        
        print("\n📊 RESULTS:")
        pprint(result)
        print(f"🕒 Execution Time: {end_time - start_time:.2f} seconds")
        return result
    except Exception as e:
        print(f"❌ Error running read_tool: {e}")
        return None


def benchmark_logic_tool(contract_data):
    print("\n" + "="*50)
    print("⏱️ BENCHMARK: logic_tool (PAL / Reasoning)")
    print("="*50)
    
    if not contract_data:
        contract_data = {
            "effective_date": "2023-01-01",
            "parties": ["Company A", "Company B"],
            "governing_law": "Delaware"
        }
        print("⚠️ Using mock data because read_tool failed or wasn't run.")
        
    question = "Is this contract active based on its effective date?"
    
    start_time = time.time()
    try:
        result = logic_tool(data=contract_data, question=question)
        end_time = time.time()
        
        print("\n📊 RESULTS:")
        pprint(result)
        print(f"🕒 Execution Time: {end_time - start_time:.2f} seconds")
        print(f"🐍 Safe-Exec Code Used: {'Yes' if result.get('code_used') else 'No'}")
    except Exception as e:
        print(f"❌ Error running logic_tool: {e}")

if __name__ == "__main__":
    extracted_data = benchmark_read_tool()
    benchmark_logic_tool(extracted_data)
