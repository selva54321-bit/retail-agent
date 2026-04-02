import requests
from core.llm import _config

def main():
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={_config['gemini_api_key']}"
    r = requests.get(url)
    if r.status_code == 200:
        models = r.json().get("models", [])
        print("Supported Models:")
        for m in models:
            if "embed" in m["name"].lower():
                print(f" - {m['name']} (supported methods: {m.get('supportedGenerationMethods', [])})")
    else:
        print(f"Failed to fetch models: {r.status_code}")

if __name__ == "__main__":
    main()
