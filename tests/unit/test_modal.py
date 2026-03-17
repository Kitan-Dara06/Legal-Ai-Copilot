import asyncio
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# CONFIG: Ensure this is your .modal.run URL from the terminal
MODAL_ENDPOINT = os.getenv("MODAL_ENDPOINT", "").strip().rstrip("/")
MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct-AWQ"


async def test_modal_judge():
    print(f"📡 Pinging Modal GPU at: {MODAL_ENDPOINT}")

    # 1. Initialize the Langchain Client (Matches Ragas setup)
    llm = ChatOpenAI(
        model=MODEL_NAME,
        api_key="dummy",
        base_url=f"{MODAL_ENDPOINT}/v1",
        temperature=0,
        max_tokens=500,
    )

    print("\n--- Test 1: Basic Connectivity ---")
    try:
        # A simple test to see if the model is alive
        res = await llm.ainvoke("Hello! State your model name and version.")
        print(f"✅ Success! Model responded:\n{res.content}")
    except Exception as e:
        print(f"❌ Connectivity Failed. Check if Modal is deployed and URL is correct.")
        print(f"Error details: {e}")
        return

    print("\n--- Test 2: Legal Logic & JSON Formatting ---")
    # This mimics a Ragas 'Faithfulness' check
    test_prompt = """
    Analyze these two statements and determine if Statement B is supported by Statement A.
    Return ONLY a JSON object: {"reasoning": "...", "verdict": 1 or 0}

    Statement A: The Executive shall be entitled to 12 months of base salary as severance.
    Statement B: The employee gets a year of pay if they are fired.
    """

    try:
        print("🤔 Asking the Judge to evaluate a legal claim...")
        res = await llm.ainvoke(test_prompt)
        print(f"✅ Judge Output:\n{res.content}")

        if "{" in res.content and "}" in res.content:
            print(
                "\n🔥 PERFECT: Model is following JSON instructions. Ragas will work."
            )
        else:
            print(
                "\n⚠️ WARNING: Model did not return clean JSON. You may need to adjust prompts."
            )

    except Exception as e:
        print(f"❌ Evaluation Test Failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_modal_judge())
