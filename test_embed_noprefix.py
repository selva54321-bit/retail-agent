import os
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from core.llm import _config
import google.generativeai as genai


genai.configure(api_key="AIzaSyBcSFLrsFiW7KWbau4HoiTui-QEmOFrVG8")

for m in genai.list_models():
    print(m.name, m.supported_generation_methods)

def main():
    try:
        emb = GoogleGenerativeAIEmbeddings(
            model="text-embedding-004",
            google_api_key=_config["gemini_api_key"]
        )
        res = emb.embed_query("test")
        print(f"Success! Embedded length: {len(res)}")
    except Exception as e:
        print(f"text-embedding-004 failed: {e}")

    try:
        emb = GoogleGenerativeAIEmbeddings(
            model="embedding-001",
            google_api_key=_config["gemini_api_key"]
        )
        res = emb.embed_query("test")
        print(f"Success embedding-001! Embedded length: {len(res)}")
    except Exception as e:
        print(f"embedding-001 failed: {e}")

if __name__ == "__main__":
    main()
