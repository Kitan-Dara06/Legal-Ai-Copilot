import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Initialize the client pointing to OpenRouter instead of OpenAI
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

def get_embedding(texts: list[str]) -> list[list[float]]:
    """
    Takes a list of text chunks and returns their BGE-M3 vector embeddings via OpenRouter.
    """
    valid_texts = [t.replace("\n", " ") for t in texts if t.strip()]

    if not valid_texts:
        return []
    try:
        response = client.embeddings.create(
            model="baai/bge-m3", # The specific OpenRouter model ID
            input=valid_texts
        )
        # Extract the list of vectors from the response
        return [data.embedding for data in response.data]
        
    except Exception as e:
        print(f"Embedding error: {e}")
        return []
