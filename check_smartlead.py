import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("SMARTLEAD_API_KEY")

if not api_key:
    print("No SMARTLEAD_API_KEY in .env")
    exit(1)

print(f"Using API Key: {api_key}")
url = f"https://server.smartlead.ai/api/v1/campaigns?api_key={api_key}"

try:
    response = requests.get(url)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        campaigns = response.json()
        print(f"Found {len(campaigns)} campaigns:")
        for c in campaigns:
            print(f"ID: {c.get('id')} | Name: {c.get('name')} | Status: {c.get('status')}")
    else:
        print(f"Error Response: {response.text}")
except Exception as e:
    print(f"Exception: {e}")
