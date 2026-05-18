import requests
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("SMARTLEAD_API_KEY")
url = f"https://server.smartlead.ai/api/v1/campaigns?api_key={api_key}"

try:
    response = requests.get(url)
    if response.status_code == 200:
        campaigns = response.json()
        if campaigns:
            print("FOUND_CAMPAIGNS:")
            for c in campaigns:
                print(f"ID: {c.get('id')} | Name: {c.get('name')}")
        else:
            print("NO_CAMPAIGNS_FOUND")
    else:
        print(f"ERROR: {response.status_code} - {response.text}")
except Exception as e:
    print(f"EXCEPTION: {str(e)}")
