import os
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from core.llm import _config

def main():
    try:
        emb = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=_config["gemini_api_key"]
        )
        res = emb.embed_query("test")
        print(f"Success! Embedded length: {len(res)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
