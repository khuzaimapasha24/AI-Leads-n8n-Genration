# 🚀 Autonomous Funeral Lead Pipeline (n8n + Python)

This project is a high-performance, AI-driven lead enrichment and outreach system. It automatically scans funeral home websites, audits their performance (PageSpeed, GMB reviews, SSL, etc.), and generates dignity-first, human-sounding cold emails using **Gemini AI (Flash 1.5/Pro)**.

The system is orchestrated via **n8n**, allowing for seamless automation from Google Sheets to **Smartlead**.

---

## 🛠️ Project Architecture
1. **n8n (Orchestrator)**: Triggers daily, reads leads from Google Sheets, sends data to the Python script, and uploads finalized leads to Smartlead.
2. **Python Pipeline (`tekscrum_pipeline.py`)**: The engine that performs deep scraping, GMB lookups via Google Places API, speed audits via PageSpeed API, and AI copy generation.
3. **Google Sheets**: Acts as your CRM/Database for storing lead status.

---

## 📋 Prerequisites

Before starting, ensure you have the following:
*   **Python 3.10+** installed on your system.
*   **n8n** installed (via npm: `npm install n8n -g`).
*   **Google Cloud Project**: With **Places API** and **PageSpeed Insights API** enabled.
*   **Gemini API Key**: From [Google AI Studio](https://aistudio.google.com/app/apikey).
*   **Smartlead API Key**: From your Smartlead account settings.

---

## 💻 Local Setup & Installation

### 1. Install Dependencies
Open your terminal (PowerShell/CMD) in the project folder and run:
```bash
pip install requests beautifulsoup4 python-dotenv google-genai
```

### 2. Configure Environment Variables
Create a file named `.env` in the root directory (or rename `.env.example`) and add your keys:
```env
GEMINI_API_KEY=your_key_here
PLACES_API_KEY=your_key_here
PAGESPEED_API_KEY=your_key_here
SENDER_NAME=YourName
```

---

## 🤖 n8n Setup Guide (Step-by-Step)

### 1. How to Start n8n
To access n8n from anywhere and allow webhooks to work correctly, start it with a tunnel:
```bash
n8n start --tunnel
```
*   **Local Access:** `http://localhost:5678`
*   **Live/Tunnel Access:** n8n will provide a URL like `https://your-subdomain.hooks.n8n.cloud`. Use this to access n8n remotely.

### 2. Import the Workflow
1. Open n8n in your browser.
2. Create a new workflow.
3. Click the **three dots (⋮)** in the top right and select **Import from File**.
4. Select `email_automation_workflow.json` from the project folder.

### 3. Connecting Google Sheets
To allow n8n to read/write to your sheet, use a **Service Account**:
1. Locate your Service Account JSON file (e.g., `xenon-lyceum-....json`).
2. In n8n, go to **Credentials** > **Add Credential** > **Google Service Account**.
3. Copy the `client_email` and `private_key` from your JSON file into n8n.
4. **IMPORTANT:** Open your Google Sheet in the browser, click **Share**, and invite your Service Account email (e.g., `n8n-bot@...iam.gserviceaccount.com`) as an **Editor**.

### 4. Connecting the Python Script
If your n8n workflow uses an `HTTP Request` node to call Python, you need a local bridge. However, it is **highly recommended** to use the **Execute Command** node instead:
1. Delete the `HTTP Request` node.
2. Add an **Execute Command** node.
3. Use the following command (replace with your actual path):
   ```bash
   python "C:\path\to\your\folder\tekscrum_pipeline.py" "{{ JSON.stringify($json) }}"
   ```

---

## 🧪 Testing the Run

### Local Test (No n8n)
To verify your API keys and Python script work correctly without opening n8n:
1. Add your leads to `input_leads.csv`.
2. Run the batch script:
   ```bash
   python run_batch.py
   ```
3. Check `output_emails.csv` for the results.

### n8n Test Run
1. In n8n, click the **Execute Workflow** button.
2. Watch the nodes turn green as they process each lead.
3. If a node turns red, click it to see the error details.

---

## 🌐 Making it Live
To run this automatically every day:
1. Ensure the **Daily Trigger** node in n8n is set to your preferred time.
2. Toggle the **Workflow Active** switch (top right) to **ON**.
3. Keep your computer/server running with `n8n start --tunnel`.

---

## 🔑 Required API Keys Summary
| Key Name | Purpose | Source |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Writes the emails | [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `PLACES_API_KEY` | Finds GMB Reviews/Rating | Google Cloud Console |
| `PAGESPEED_API_KEY` | Checks website speed | Google Cloud Console |
| `SMARTLEAD_API_KEY` | Uploads leads for sending | Smartlead Dashboard |

---

## 🛑 Troubleshooting
*   **Redirect URI Mismatch:** Ensure you are accessing n8n via the exact URL registered in Google Cloud Console.
*   **Service Account Error:** Double-check that you shared the Google Sheet with the service account's email address.
*   **Gemini Quota Error:** If you see "Model Exhausted," your free tier limit is reached. Wait or swap to a new API key.
