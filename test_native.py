import requests
from core.llm import _config

def get_native_embedding(text: str, model="text-embedding-004", version="v1beta"):
    url = f"https://generativelanguage.googleapis.com/{version}/models/{model}:embedContent?key={_config['gemini_api_key']}"
    payload = {
        "model": f"models/{model}",
        "content": {"parts": [{"text": text}]}
    }
    
    r = requests.post(url, json=payload)
    if r.status_code == 200:
        emb = r.json().get("embedding", {}).get("values", [])
        print(f"Success! [{version} | {model}] : length {len(emb)}")
    else:
        print(f"Failed [{version} | {model}]: {r.status_code} {r.text}")

def main():
    print("Testing Native Gemini Endpoints...\n")
    versions = ["v1alpha", "v1beta", "v1"]
    models = ["text-embedding-004", "embedding-001"]
    
    for v in versions:
        for m in models:
            get_native_embedding("Hello world", model=m, version=v)

if __name__ == "__main__":
    main()
