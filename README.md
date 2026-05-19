# Invoxa — Project Evaluation

**Course Assignment Submission**
**Project:** AI-powered Expense Invoice Productivity Agent
**Stack:** Streamlit · LangGraph · GPT-4o · Firebase · Google Drive · Google Sheets · ChromaDB

> 📦 **Looking to run the project?** See the [Setup & Installation Guide](SETUP.md).

---

## Table of Contents

1. [Agent Purpose](#1-agent-purpose)
2. [Core Functionality](#2-core-functionality)
3. [User Interface](#3-user-interface)
4. [Technical Implementation](#4-technical-implementation)
5. [Documentation](#5-documentation)
6. [Optional Tasks Completed](#6-optional-tasks-completed)

---

## 1. Agent Purpose

### What Invoxa Does
Invoxa is an AI-powered expense management agent that automates the full lifecycle of business invoice processing — from file upload through data extraction, review, organisation, anomaly detection, and reporting — using a multi-step LangGraph pipeline with Human-in-the-Loop (HITL) oversight at key decision points.

### Why It Is Useful
Managing business invoices manually is slow, error-prone, and hard to audit. Invoxa solves this by:
- Eliminating manual data entry — GPT-4o vision extracts all fields automatically from PDFs or images
- Keeping files organised — invoices are renamed and moved to the correct Google Drive month folder automatically
- Catching mistakes — duplicate invoice detection and unusual amount warnings prevent costly errors
- Providing instant insights — natural language chat lets users query their expense data without building reports manually
- Maintaining full control — every AI decision passes through a human review screen before being committed

### Target Users
- Freelancers and sole traders managing their own invoices
- Small business owners without a dedicated accounting team
- Accountants or finance assistants processing high volumes of invoices
- Anyone using Google Drive as their document management system

---

## 2. Core Functionality

![Invoxa Architecture](project-diagram.svg)

### 2.1 Invoice Upload & Extraction (Upload Invoice page)

The Upload Invoice page has two entry points — manual file upload and Gmail inbox scan — both feeding into the same LangGraph pipeline.

#### 2.1a Manual Upload

1. **File Upload** — Accepts PDF, JPG, JPEG, PNG, WEBP
2. **Google Drive Upload** — File is immediately saved to the user's `Expenses/` root folder
3. **GPT-4o Vision Extraction** — The agent downloads the file from Drive and extracts:
   - Supplier name, invoice number, invoice date (YYYY-MM-DD)
   - Total amount, currency (ISO code), tax amount, tax rate
   - Category (from user-defined list), description
   - **Line items array** — every line on the invoice: description, quantity, unit, unit price, line total (returns `[]` for single-total receipts with no itemised breakdown)

**PDF Processing strategy (cascading fallback):**
- PyMuPDF → converts pages to 2× zoom JPEG (preferred, no external dependencies)
- pdf2image + poppler → fallback if PyMuPDF fails
- PyPDF2 text extraction → last resort for text-based PDFs

**Extraction prompt** is dynamically built with today's date injected to prevent year misidentification (e.g. GPT guessing 2023 instead of 2026), and uses the user's custom category list.

#### 2.1b Gmail Inbox Scan & Import

A dedicated Gmail tab lets users scan their inbox for invoice emails without leaving Invoxa:

1. **Scan** — Fetches up to 30 unread emails with attachments (PDF, JPEG, PNG, WebP, GIF) that haven't been labelled `invoxa-processed` yet, using the Gmail API
2. **AI Classification** — Each attachment is passed to `gpt-4o-mini` with the email subject, sender, and filename; the model returns `{ is_invoice, confidence }` — only candidates with confidence ≥ 0.65 are shown
3. **Review cards** — Each candidate renders as a card: filename, sender, subject, date, size, and a colour-coded confidence badge (green ≥ 85%, amber otherwise)
4. **One-click Import** — Clicking Import downloads the attachment, uploads it to `Expenses/Inbox/` in Drive, labels the Gmail message `invoxa-processed`, saves an import record in Firestore, and routes the file through the full HITL extraction pipeline
5. **Already-imported guard** — `is_already_imported()` checks Firestore before classification so previously imported attachments are silently skipped

The classification logic lives in `agent/nodes/classify_email.py` with its prompt in `agent/prompts/email_classification_prompt.py`, following the same node/prompt pattern as the rest of the agent.

### 2.2 Human-in-the-Loop (HITL) — Two Interrupt Points

**HITL 1 — Data Review (before rename_and_organize node)**

After extraction the graph pauses via `interrupt_before=["rename_and_organize"]`. Streamlit renders a two-column review screen where the user can:
- Edit any extracted field (supplier, date, amount, tax, currency, description)
- Select or create a new category on the fly
- Customise the suggested filename
- Review extracted **line items** in a read-only table (description, qty, unit price, line total); a warning is shown if the items sum differs from the invoice total by more than 1%
- Confirm (resumes graph) or Cancel (deletes Drive file + Firestore record)

The right column shows a live **document preview** alongside the form: images render with `st.image`, PDFs are embedded via a base64 `<iframe>` — so the user can cross-check extracted fields against the original document without switching tabs.

On confirm, `graph.update_state()` injects the user's edits and `graph.invoke(None)` resumes the graph from the interrupt checkpoint.

**HITL 2 — Anomaly Review (after check_anomalies node)**

If `check_anomalies` detects any warnings, the pipeline pauses in Streamlit (not at the graph level) and shows:
- 🔴 Duplicate invoice — same supplier + same amount (within 1%) + same date
- 🟡 Unusual amount — invoice exceeds 2× the supplier's historical average
- 🟡 Missing recurring supplier — supplier appeared in 2+ prior months but absent this month

The user can **Keep** (proceed to done) or **Discard** (delete from Drive and Firestore).

### 2.3 Anomaly Detection

The `check_anomalies` node uses three detection algorithms:

| Check | Criteria |
|---|---|
| **Duplicate** | Supplier similarity ≥ 82% (difflib fuzzy match) + amount within 1% + identical date |
| **Unusual Amount** | Invoice amount > 2× supplier's historical average (requires ≥2 prior invoices) |
| **Missing Recurring** | Supplier appeared in ≥ 2 prior months but absent from current month |

Duplicate detection uses `difflib.SequenceMatcher` to catch typos and abbreviations (e.g. "Amazon" vs "Amazon EU").

### 2.4 File Organisation

After HITL approval, the `rename_and_organize` node:
- Creates the correct month folder (`Expenses/March 2026/`) if it doesn't exist
- Atomically renames and moves the file in a single Google Drive API call
- Updates the Firestore record with the new filename
- Logs the activity for auditing

### 2.5 Monthly Reporting

The `generate_report` node creates or refreshes a Google Sheets spreadsheet:
- **Monthly tab** — full invoice table with category, supplier, amounts, tax
- **Year Summary tab** — cross-month totals grouped by month
- **Line Items tab** — one row per line item across all invoices in the month (skipped if no invoices have itemised lines); columns: Invoice Date, Supplier, Description, Qty, Unit, Unit price, Line total, Category
- The spreadsheet is saved inside the `Expenses/` Drive folder
- Report URL is returned to the UI for a one-click "Open in Sheets" button

The Monthly Report page also renders inline charts:
- Category breakdown bar chart
- Top 5 suppliers bar chart
- Month-over-month line chart for the selected year

### 2.6 Expense Chat with RAG

The `chat_with_expenses` node provides a natural language interface powered by a hybrid retrieval strategy:

**Hybrid context strategy:**
- **≤ 50 invoices** — full-context injection: all invoice fields, supplier data, and line items are loaded directly into the system prompt
- **> 50 invoices** — semantic RAG: a ChromaDB vector index is queried for the 8 most relevant chunks, keeping the prompt focused and cost-efficient

**ChromaDB vector index:**
- Per-user collection (`invoices_{uid}`) stored on disk with cosine similarity (HNSW)
- Embedding model: `text-embedding-3-small` (OpenAI, 1 536 dimensions)
- Three chunk types per invoice: `header` (full metadata summary), `line_items` (product descriptions), `pointer` (lightweight, used for preview lookup)
- Index is rebuilt automatically on cold-start (Streamlit Cloud restart) with a progress bar; manual rebuild available in Settings → Search Index

**Invoice preview:**
- Intent classifier (`gpt-4o-mini`, max 15 tokens) detects "show me / preview / open the X invoice" phrasing and short-circuits the LLM entirely
- Semantic search over pointer chunks finds the best-matching invoice by supplier name, date, or invoice number
- Renders a high-resolution thumbnail (fetched via Google Drive API + OAuth2) and an "Open in Google Drive" button directly in the chat thread
- Preview card survives Streamlit reruns via session state

**Other chat features:**
- Uses `gpt-4o-mini` for cost efficiency; tenacity retry (3 attempts, exponential backoff)
- Maintains a 20-turn conversation history
- Suggested question chips including product-level queries ("What did I buy from Amazon?") and preview prompts ("Show me the latest invoice")
- **Clear Conversation** button resets history in one click

### 2.7 Supplier Long-Term Memory

Every time an invoice is saved, `_update_supplier_memory()` upserts a supplier summary document in Firestore:
- `total_spend` — cumulative spend across all invoices
- `invoice_count` — number of invoices processed
- `first_seen` / `last_seen` — timestamps

This memory powers anomaly detection (average amount calculation, recurring supplier tracking) and enriches the chat context.

---

## 3. User Interface

### Navigation
- Single `app.py` entry point with custom sidebar radio navigation
- Streamlit's auto-generated multipage nav hidden via CSS
- Sidebar starts collapsed by default for a cleaner initial view
- Single-click navigation (rerun triggered on change)

### Pages

| Page | Purpose |
|---|---|
| 🏠 **Dashboard** | Quick stats, recent activity with delete and editable line items expander, category chart, AI cost metric |
| ⬆️ **Upload Invoice** | Full HITL pipeline (upload → extract → review → anomaly check → done) |
| 📊 **Monthly Report** | Month/year selector, charts, generate/refresh Google Sheets report |
| 💬 **Chat** | Expense Q&A with RAG, invoice preview cards, suggested question chips, and conversation history |
| ⚙️ **Settings** | Drive/Sheets config, currency, category management, AI cost monitoring, search index health, account |

### Dashboard Header
- Invoxa logo (emoji + name + tagline) on the left
- User profile (avatar, name, email, current month) compact on the right
- 5 metric cards: Total Expenses, Total Tax, Invoices Processed, Unique Suppliers, 🤖 AI Cost

### Category Management
- User-editable category list in Settings (one per line text area)
- Changes persisted to Firestore and applied immediately to:
  - GPT-4o extraction prompt (guides AI classification)
  - HITL review form (drives the category dropdown)
- New categories can be created on-the-fly during HITL review

---

## 4. Technical Implementation

### Architecture

```
Streamlit (UI) ──► graph.invoke() ──► LangGraph Pipeline
                                          │
                    ┌─────────────────────┼──────────────────────┐
                    │                     │                      │
               upload_invoice      generate_report            chat
                    │
          extract_invoice_data (GPT-4o vision)
                    │
          suggest_filename → ⏸ HITL 1 (Streamlit form)
                    │
          rename_and_organize (Google Drive)
                    │
          check_anomalies → ⏸ HITL 2 (if warnings)
                    │
                   END
```

### Agent Framework: LangGraph

![Agent Graph](agent-graph.svg)

- `StateGraph` with `AgentState` TypedDict for typed state management
- `MemorySaver` checkpointer for thread-based HITL state persistence
- `interrupt_before=["rename_and_organize"]` for the first HITL pause
- `graph.update_state()` + `graph.invoke(None)` pattern for resuming from interrupts
- `thread_id` (UUID per upload) ensures isolated state per user session
- Conditional routing via `route_action()` dispatcher (upload / report / chat)

### LLM Usage

| Model | Node | Settings | Purpose |
|---|---|---|---|
| `gpt-4o` | extract_invoice_data | temp=0, max_tokens=2000 | Invoice vision extraction + line items |
| `gpt-4o-mini` | chat_with_expenses | temp=0.3, max_tokens=1024 | Expense Q&A |
| `gpt-4o-mini` | query_router | temp=0, max_tokens=15 | Chat intent classification (preview / search / general) |
| `gpt-4o-mini` | classify_email | temp=0, max_tokens=60 | Gmail invoice classification |
| `text-embedding-3-small` | chroma_service | — | Invoice chunk embeddings for semantic search |

### Memory

**Short-term (session):**
- `st.session_state` — user credentials, current page, upload pipeline state
- `MemorySaver` — LangGraph checkpoint per `thread_id`, survives HITL interrupt/resume

**Long-term (Firestore):**
- `users/{uid}/invoices/` — all extracted invoice records (includes `line_items_count` and `line_items_total` summary fields)
- `users/{uid}/invoices/{id}/line_items/` — individual line item documents (description, qty, unit, unit price, line total, tax)
- `users/{uid}/suppliers/` — cumulative supplier spend/count memory
- `users/{uid}/ai_usage/` — per-call token and cost logs
- `users/{uid}/` profile — settings, categories, running AI cost total

**Vector index (ChromaDB — local disk):**
- Path: `chroma_db/` (excluded from git)
- Collection `invoices_{uid}` — per-user, cosine similarity
- Chunks: `header` + `line_items` + `pointer` per invoice; upsert-safe
- Rebuilt automatically on cold start; CLI backfill via `utils/backfill_chroma.py`

### Error Handling

- **Tenacity retry** on all OpenAI and Drive API calls: exponential backoff (2–30s), 3 attempts max
- **Cascading PDF fallback**: PyMuPDF → pdf2image → PyPDF2 text
- **Graceful degradation**: Firestore failures return empty lists; year summary failure doesn't block monthly report
- **Contextual logging**: `log_error(uid, context, message, details)` stores structured error records in Firestore
- **User feedback**: `st.error()` / `st.warning()` / `st.status()` state indicators throughout the upload pipeline

### AI Cost Monitoring

Every OpenAI API call is metered:
- `response.usage` captured (prompt_tokens, completion_tokens)
- Cost calculated: `(prompt × input_price + completion × output_price) / 1_000_000`
- Stored in `users/{uid}/ai_usage/` with model, action, invoice_id, timestamp
- `firestore.Increment()` atomically updates running total on user profile
- Visible in Settings (by-model and by-action breakdown tables) and Dashboard (total cost metric)

**Pricing table (hardcoded, update as OpenAI changes rates):**
| Model | Input $/1M | Output $/1M |
|---|---|---|
| gpt-4o | $2.50 | $10.00 |
| gpt-4o-mini | $0.15 | $0.60 |

### Observability: LangSmith

- Env vars set via direct `tomllib` parse of `secrets.toml` **before** any LangGraph import — critical because `graph = build_graph()` runs at module import time
- EU region endpoint configured: `https://eu.api.smith.langchain.com`
- Zero-code tracing: every `graph.invoke()` automatically sends node-by-node traces to LangSmith
- `langgraph.json` created for LangGraph Studio local debugging (`langgraph dev`)

### Local Debugging: LangGraph Studio

`langgraph.json` in the project root registers the compiled graph for LangGraph Studio:

```json
{
  "dependencies": ["."],
  "graphs": { "invoxa": "./agent/graph.py:graph" },
  "env": ".streamlit/secrets.toml"
}
```

**Running the local debug server:**

```bash
pip install "langgraph-cli[inmem]"
langgraph dev   # starts server at http://localhost:2024
```

Then open the **LangGraph Studio** desktop app and connect to `http://localhost:2024`.

**What LangGraph Studio shows:**

| Feature | Details |
|---|---|
| **Visual graph diagram** | Live rendering of all nodes and edges, matching the pipeline in Section 2 |
| **Node-by-node execution** | Step through each node (upload → extract → suggest → HITL → organize → anomalies) |
| **State inspector** | Full `AgentState` snapshot after every node — inspect extracted fields, warnings, filenames |
| **HITL controls** | Trigger and resume `interrupt_before` pauses directly in the Studio UI without Streamlit |
| **Run replay** | Re-run any previous invocation from a saved checkpoint for debugging |

### Authentication

- Firebase Authentication with Google OAuth2
- `google_credentials` OAuth2 object stored in session state, passed to all Drive/Sheets calls
- Firebase Admin SDK for ID token verification (lazy-initialized on first Firestore call)
- Secrets managed via `.streamlit/secrets.toml` (never committed to git)

### Libraries Used

| Library | Version | Purpose |
|---|---|---|
| streamlit | ≥1.32.0 | Web UI framework |
| langgraph | ≥0.2.0 | Stateful agent graph |
| langchain | ≥0.2.0 | LLM utilities |
| langchain-openai | ≥0.1.0 | OpenAI integration |
| langsmith | ≥0.7.0 | Observability & tracing |
| openai | ≥1.30.0 | GPT-4o / GPT-4o-mini API |
| firebase-admin | ≥6.5.0 | Firestore Admin SDK |
| google-api-python-client | ≥2.128.0 | Drive & Sheets APIs |
| google-auth-oauthlib | ≥1.2.0 | Google OAuth2 flow |
| pymupdf | ≥1.23.0 | PDF to image (preferred) |
| pdf2image | ≥1.17.0 | PDF to image (fallback) |
| PyPDF2 | ≥3.0.0 | PDF text extraction (fallback) |
| pandas | ≥2.2.0 | Data tables and charts |
| tenacity | ≥8.3.0 | Retry with exponential backoff |
| Pillow | ≥10.3.0 | Image processing |
| chromadb | ≥0.5.0 | Local vector store for semantic invoice search |

---

## 5. Documentation

### How to Run Locally

```bash
# 1. Clone and set up environment
git clone https://github.com/bspyrop/invoxa
cd invoxa
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure secrets
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Fill in: OPENAI_API_KEY, Firebase config, Google OAuth credentials, LangSmith key

# 3. Run
streamlit run app.py

# 4. (Optional) LangGraph Studio for visual debugging
langgraph dev  # then open LangGraph Studio app → http://localhost:2024
```

### Common Use Cases

**Upload and process an invoice:**
1. Open Upload Invoice page
2. Drag and drop a PDF or image
3. Wait for GPT-4o to extract data (~3–5 seconds)
4. Review and edit any fields in the HITL form — the original document is previewed side by side
5. Check the extracted line items table; a warning appears if the items sum doesn't match the total
6. Select or create a category
7. Click Confirm — file is moved to `Expenses/March 2026/` in Drive; line items saved to Firestore sub-collection
8. If a duplicate is detected, choose Keep or Discard

**View or edit line items on an existing invoice:**
1. Open Dashboard → Recent Activity
2. Expand the **Line items** section on any invoice card
3. Edit descriptions, quantities, or prices directly in the table
4. Click **Save changes** — updates are written to Firestore immediately

**Import invoices from Gmail:**
1. Open Upload Invoice page → Check email tab
2. Click Scan inbox for invoices
3. Review the candidate cards (sender, subject, confidence score)
4. Click Import on any card — the attachment is downloaded, uploaded to Drive, and sent through the full extraction + HITL pipeline automatically

**Generate a monthly report:**
1. Open Monthly Report page
2. Select month and year
3. Click Generate Report
4. View charts inline or click Open in Sheets

**Query your expenses in natural language:**
1. Open Chat page
2. Click a suggested question or type your own
3. Examples:
   - "What is my total tax for this year?"
   - "Which supplier costs the most?"
   - "Show me all invoices over €500 in February"
   - "What did I spend on software subscriptions?"
   - "What did I buy from Amazon?" *(searches line items via RAG)*
   - "Which invoice had an EC2 charge?" *(semantic product search)*

**Preview an invoice from the chat:**
1. Open Chat page
2. Type a preview request, e.g. "Show me the KORA invoice" or "Preview the Anthropic invoice"
3. The agent finds the invoice semantically (no exact name needed), displays a high-resolution thumbnail, and provides a direct link to open it in Google Drive

**Backfill the ChromaDB index for existing invoices:**
```bash
python utils/backfill_chroma.py --uid <firebase_uid>           # index new invoices only
python utils/backfill_chroma.py --uid <firebase_uid> --reset   # drop and re-index everything
python utils/backfill_chroma.py --uid <firebase_uid> --dry-run # preview without writing
```
Standalone CLI — no Streamlit required. Reads invoices and line items directly from Firestore.

**Backfill line items for existing invoices:**
```bash
python utils/backfill_line_items.py --uid <firebase_uid> [--dry-run]
```
Standalone CLI — no Streamlit required. Handles its own Google OAuth2 flow (opens a browser once, caches the token to `.backfill_token.json`). Skips invoices that already have `line_items_count > 0`.

**Manage categories:**
1. Open Settings → Category Labels
2. Edit the list (one per line)
3. Save — takes effect on the next invoice upload

**Monitor AI costs:**
1. Open Settings → AI Cost Monitoring
2. View total cost, breakdown by model (gpt-4o vs gpt-4o-mini), and breakdown by action (extract vs chat)
3. Expand full log to see per-call costs

### Technical Decisions

| Decision | Rationale |
|---|---|
| **LangGraph over plain LangChain** | Native HITL support via `interrupt_before` + `MemorySaver` checkpointing |
| **GPT-4o for extraction, GPT-4o-mini for chat** | Vision capability needed for extraction; mini sufficient for chat at lower cost |
| **PyMuPDF over pdf2image** | No external poppler dependency, faster, works on all platforms |
| **Firestore over SQL** | Schema-free suits variable invoice fields; Firebase Admin SDK included in Firebase Auth stack |
| **Hybrid RAG (full-context ≤50, ChromaDB >50)** | Full context is cheaper and more accurate at low volume; RAG keeps prompts focused and cost-efficient at scale |
| **ChromaDB PersistentClient over hosted vector DBs** | Zero additional infrastructure; on-disk index survives restarts and is rebuilt automatically from Firestore on cold start |
| **text-embedding-3-small for embeddings** | Best cost/quality ratio at $0.02/1M tokens; 1 536 dims sufficient for invoice text |
| **Pointer chunks for preview lookup** | Lightweight chunk type separates preview intent from content retrieval, avoiding full invoice text in preview searches |
| **difflib for duplicate detection** | Built-in Python, no extra dependency; handles OCR variations and abbreviations |
| **Category stored per-user in Firestore** | Users have different business domains; hardcoded defaults ship with sensible defaults |
| **tomllib for LangSmith env setup** | LangGraph graph is built at module import time — must set env vars before any import |

---

## 6. Optional Tasks Completed

### Easy
| Task | Status | Details |
|---|---|---|
| Give the agent a personality | ✅ | Chat system prompt defines Invoxa as a clear, data-grounded expense assistant |

### Medium
| Task | Status | Details |
|---|---|---|
| **Calculate and display token usage and costs** | ✅ | Full per-call logging to Firestore; dashboard metric + settings breakdown table |
| **Add retry logic for agents** | ✅ | Tenacity decorator on all OpenAI and Drive API calls (3 attempts, exponential backoff) |
| **Implement long-term and short-term memory** | ✅ | Short-term: `MemorySaver` + session state. Long-term: Firestore invoices + supplier memory |
| **Implement one more function tool calling an external API** | ✅ | Google Sheets API integration for report generation (separate from Drive) |
| **Add user authentication and personalisation** | ✅ | Firebase Auth + Google OAuth2; per-user settings, categories, Drive folder paths |

### Hard
| Task | Status | Details |
|---|---|---|
| **Add LLM observability tool** | ✅ | LangSmith integrated with EU region endpoint; `langgraph.json` for LangGraph Studio |
| **Implement agent that integrates with external data sources** | ✅ | Agent reads Google Drive (file storage), Google Sheets (reporting), Firestore (memory) — three external integrations in a unified pipeline |
| **Implement RAG for scalable chat** | ✅ | ChromaDB vector index with `text-embedding-3-small`; hybrid strategy (full-context ≤50 invoices, semantic RAG above); invoice preview via pointer chunks |

---

*Invoxa v1.0 — Built with LangGraph + GPT-4o + Google Drive + Firebase*
