# Invoxa — Setup & Installation Guide

> ← Back to [Project Evaluation (README)](README.md)

A Streamlit application that automates monthly expense management using LangGraph, GPT-4o, Google Drive, Google Sheets, Gmail, and Firebase.

---

## Features

- **Google Sign-In** via Firebase Auth (OAuth2)
- **Invoice extraction** — GPT-4o vision reads PDFs and images from Google Drive
- **Gmail import** — scan your inbox for invoice attachments and import them directly into Drive
- **Line item extraction** — GPT-4o pulls individual products, quantities, and unit prices from each invoice
- **Human-in-the-loop** — review and edit every extracted field before renaming
- **Smart file naming** — `AWS_Software_2025-03-15_150EUR.pdf`
- **Google Sheets reports** — monthly tabs + year summary, auto-generated
- **Anomaly detection** — duplicates, missing recurring suppliers, unusual amounts
- **Chat interface** — ask questions about your expenses in plain English; RAG search over product-level details
- **Invoice preview** — "Show me the ACME invoice" returns a thumbnail + Google Drive link
- **Long-term memory** — supplier history and invoice index stored in Firestore + ChromaDB

---

## Prerequisites

| Service | What you need |
|---|---|
| OpenAI | API key with GPT-4o and GPT-4o-mini access |
| Firebase | Project with Auth (Google provider) + Firestore enabled |
| Google Cloud | OAuth2 credentials + Drive API + Sheets API + Gmail API enabled |

---

## Setup

### 1. Clone and create virtual environment

```bash
git clone <repo-url>
cd invoxa
python -m venv venv --without-pip
source venv/bin/activate
curl https://bootstrap.pypa.io/get-pip.py | python
pip install -r requirements.txt
```

> **macOS note:** `pdf2image` requires poppler. Install with:
> ```bash
> brew install poppler
> ```

---

### 2. Configure Firebase

1. Go to [Firebase Console](https://console.firebase.google.com/) and create a project.
2. Enable **Authentication → Sign-in method → Google**.
3. Enable **Firestore Database** (start in production mode).
4. Go to **Project Settings → Service accounts** and generate a new private key (downloads a JSON file).

---

### 3. Configure Google Cloud OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Select your Firebase project.
3. Enable these APIs:
   - Google Drive API
   - Google Sheets API
   - Gmail API
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**.
5. Application type: **Web application**.
6. Add Authorised redirect URIs:
   - `http://localhost:8501` (local dev)
   - `https://your-app.streamlit.app` (production)
7. Note the **Client ID** and **Client Secret**.

---

### 4. Fill in secrets

Copy `.streamlit/secrets.toml` and fill in all values:

```toml
OPENAI_API_KEY = "sk-..."

FIREBASE_API_KEY            = "AIza..."
FIREBASE_AUTH_DOMAIN        = "your-project.firebaseapp.com"
FIREBASE_PROJECT_ID         = "your-project-id"
FIREBASE_STORAGE_BUCKET     = "your-project.appspot.com"
FIREBASE_MESSAGING_SENDER_ID = "123456789"
FIREBASE_APP_ID             = "1:...:web:..."

FIREBASE_ADMIN_CREDENTIALS = """
{ ...paste service account JSON here... }
"""

GOOGLE_CLIENT_ID     = "xxx.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-..."
GOOGLE_REDIRECT_URI  = "http://localhost:8501"
```

---

### 5. Run locally

```bash
streamlit run app.py
```

Visit `http://localhost:8501`.

#### Backfill ChromaDB index (first run or after data migration)

If you already have invoices in Firestore and need to populate the vector index:

```bash
python utils/backfill_chroma.py --uid <your-firebase-uid>
# Options:
#   --dry-run   preview what would be indexed without writing
#   --reset     drop and rebuild the existing index from scratch
```

---

### 6. Google Drive structure

Create this folder structure in the Google Drive of the account that will sign in:

```
Expenses/
├── January 2025/
│   ├── AWS_Invoice.pdf
│   └── ...
├── February 2025/
└── ...
```

The root folder name ("Expenses") is configurable in **Settings**.

---

## Deploy to Streamlit Community Cloud

1. Push your code to GitHub (**do not** commit `.streamlit/secrets.toml`).
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your repo.
3. Set the main file to `app.py`.
4. In **Advanced settings → Secrets**, paste the contents of your `secrets.toml`.
5. Update `GOOGLE_REDIRECT_URI` in secrets to your deployed app URL.
6. Add the deployed URL to your Google OAuth2 authorised redirect URIs.

---

## Project structure

```
invoxa/
├── app.py                         Main entry point
├── requirements.txt
├── .streamlit/secrets.toml        API keys (gitignored)
├── auth/
│   └── firebase_auth.py           OAuth2 + Firebase login flow
├── agent/
│   ├── state.py                   AgentState TypedDict
│   ├── graph.py                   LangGraph compiled graph
│   ├── nodes/
│   │   ├── list_invoices.py       Drive file enumeration
│   │   ├── extract_data.py        GPT-4o vision extraction
│   │   ├── suggest_filename.py    Filename generation
│   │   ├── rename_organize.py     Drive rename + move
│   │   ├── check_anomalies.py     Duplicate / missing / unusual checks
│   │   ├── generate_report.py     Google Sheets report writer
│   │   └── chat.py                GPT-4o-mini chat node
│   └── prompts/
│       ├── extraction_prompt.py
│       └── chat_prompt.py
├── services/
│   ├── google_drive.py            Drive API wrapper
│   ├── google_sheets.py           Sheets API wrapper
│   ├── firestore.py               Firestore read/write
│   ├── gmail_service.py           Gmail API — scan inbox for invoice attachments
│   ├── chroma_service.py          ChromaDB vector index (RAG)
│   ├── query_router.py            Intent classifier (preview / search / general)
│   └── invoice_preview.py        Find and return Drive preview metadata
├── pages/
│   ├── dashboard.py
│   ├── process_invoices.py
│   ├── monthly_report.py
│   ├── chat.py                    Chat + RAG + invoice preview card
│   └── settings.py                Settings + search index management
├── utils/
│   ├── session.py                 Session state helpers
│   ├── helpers.py                 Shared utilities
│   └── backfill_chroma.py         CLI — rebuild ChromaDB index from Firestore
└── chroma_db/                     Local ChromaDB vector store (gitignored)
```

---

## Architecture

```
User → Streamlit UI
          │
          ├── auth/firebase_auth.py  (Google OAuth2 → Firebase session)
          │
          └── LangGraph Agent
                ├── list_invoices     → Google Drive API
                │                        └── Gmail API (optional inbox scan)
                ├── extract_data      → GPT-4o vision + line item extraction
                ├── suggest_filename  → deterministic
                ├── rename_organize   → Google Drive API → ChromaDB index
                ├── check_anomalies   → Firestore
                ├── generate_report   → Google Sheets API
                └── chat              → intent router (gpt-4o-mini)
                                           ├── preview  → Drive thumbnail + link
                                           ├── search   → ChromaDB RAG (top-k)
                                           └── general  → full context (≤50 inv.)
```

---

## License

MIT
