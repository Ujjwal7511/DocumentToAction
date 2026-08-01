# Document-to-Action Project Assistant

Turn up to three project documents (meeting notes, requirement drafts, implementation notes, project updates, decision records) into a **human-reviewed** action summary.

The AI extracts facts, decisions, assumptions, risks, open questions, and **proposed** action items. It never creates or assigns tasks on its own — every action stays `approved=False` until you explicitly approve it in the UI.

---

## Quick start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env from the template
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
```

The app auto-loads `.env` and **requires MongoDB** — it fails loudly at import
time if `MONGODB_URI` is missing. There is no SQLite/SQLAlchemy fallback.

### MongoDB Atlas setup (free tier)

1. **Create a free cluster** — go to <https://www.mongodb.com/atlas>, sign up,
   click **Build a Database**, choose the **M0 Free (Shared)** tier, pick any
   cloud provider/region, and click **Create**.
2. **Add a database user** — in **Database Access → Add New Database User**,
   create a username and a strong password (use the password authentication
   method). You'll paste these into the connection string below.
3. **Allow network access** — in **Network Access → Add IP Address**, choose
   **Allow Access from Anywhere** (`0.0.0.0/0`) for a quick start, or add your
   specific IP for tighter security.
4. **Copy the connection string** — in the cluster, click **Connect → Drivers**.
   It will look like:

   ```
   mongodb+srv://<db_user>:<db_password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
   ```

5. **Paste it into `.env`** (replace the placeholders with your real database
   user and password):

   ```
   MONGODB_URI=mongodb+srv://your_username:your_password@your_cluster.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DB_NAME=DocumentToAction
   ```

   `MONGODB_DB_NAME` is optional (defaults to `document_to_action`). The app
   creates its collections (`project_sessions`, `documents`, `extracted_items`,
   `conflicts`, `action_items`, `kb_matches`, `reviewed_summaries`, `users`,
   `counters`) in that database automatically.

Get a free Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) and put it in `.env`:

```
GEMINI_API_KEY=your_actual_key_here
GEMINI_MODEL=gemini-2.0-flash
LLM_PROVIDER=gemini
```

```bash
# 4. Run the app
streamlit run app.py
```

### Demo path (no prep beyond the steps above)

1. Click **Load sample documents** (loads the three files in `sample_docs/`).
2. Click **Run analysis**.
3. Review each document tab, edit items, resolve the intentional deadline conflict (March 18 / 20 / 28), approve or reject proposed actions.
4. Open **Review Summary** and save the final summary.

Without an API key the app still runs in **heuristic mode** so you can exercise the full UI and data model; LLM quality is better with Gemini configured.

---

## Architecture

```
Upload (.txt / .md / .docx)
        │
        ▼
   ingest.py  ── parse + type detection (LLM or heuristic)
        │
        ▼
   extract.py ── structured JSON extraction via llm_client.generate_json
        │         (facts, decisions, assumptions, risks, questions,
        │          proposed actions, suggested gaps)
        ▼
 conflicts.py ── cross-document conflict / repetition detection
        │
   kb.py      ── TF-IDF retrieval over knowledge_base/
        │
      db.py   ── MongoDB persistence (PyMongo)
        │
     app.py   ── Streamlit review UI (edit → approve → save summary)
```

| Module | Role |
|--------|------|
| `db.py` | PyMongo CRUD. `ActionItem.approved` defaults `False`; `add_action_item()` always writes `approved=False`. |
| `llm_client.py` | Provider-agnostic `generate_json(prompt, schema)` with retry-on-malformed-JSON. |
| `ingest.py` | `python-docx` / plain-text parsing, section splitting, type detection. |
| `extract.py` | Extraction schemas, source-offset linking, gap suggestions. Never sets `approved=True`. |
| `conflicts.py` | Heuristic + LLM conflict/repetition detection. |
| `kb.py` | Loads markdown/JSON standards; scikit-learn TF-IDF cosine similarity. |
| `app.py` | Streamlit UI — upload, editors, conflict resolution, approval, summary. |

MongoDB collections: `project_sessions`, `documents`, `extracted_items`, `conflicts`, `action_items`, `kb_matches`, `reviewed_summaries`, `users`, `counters`.

---

## Design decisions

### Confirmed vs interpreted

Every extracted item stores `is_confirmed`:

- `True` — the model (or heuristic) treats the content as a **direct statement** in the source.
- `False` — the content is an **inference / interpretation** (including all suggested gaps).

The UI surfaces this as a Confirmed / Interpreted pill and keeps source section + character offsets so you can jump back to evidence.

### No automatic task assignment

Enforced at multiple layers:

1. **Schema default** — `ActionItem.approved = False`, `status = "proposed"`.
2. **Write path** — `add_action_item()` always writes `approved=False`; `extract.py` only calls that helper.
3. **Approval API** — only `approve_action_item()` / UI **Approve** buttons flip `approved` to `True`.
4. **Summary filter** — `build_reviewed_summary_payload()` includes actions only when `approved and not rejected`.

There is no code path where the extraction pipeline marks an action approved.

### Provider-agnostic LLM wrapper

`extract.py` and friends call `llm_client.generate_json` only. To fail over when Gemini free-tier quota is exhausted:

```
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_API_KEY=...
OPENAI_COMPAT_MODEL=llama-3.3-70b-versatile
```

No changes to extraction logic are required.

### Knowledge base without a vector DB

The `knowledge_base/` folder holds 6 example standards (API rollback, auth security review, decision ownership, PII retention, production observability, dependency upgrades). Retrieval uses TF-IDF + cosine similarity — appropriate for a small, curated corpus.

### Sample documents intentionally conflict

The bundled samples disagree on the partner migration deadline (March 18 / 20 / 28) so conflict detection has something real to surface in a demo.

---

## Project layout

```
├── SPEC.md
├── app.py
├── ingest.py
├── llm_client.py
├── extract.py
├── kb.py
├── conflicts.py
├── db.py
├── knowledge_base/          # organizational standards
├── sample_docs/             # demo inputs
├── requirements.txt
├── .env.example
└── README.md
```

---

## Edge cases handled

| Case | Behavior |
|------|----------|
| Empty document | Stored; extraction skipped; UI shows empty-state message. |
| No extractable items | Empty tables + informational captions. |
| Malformed LLM JSON | Retried up to `LLM_MAX_RETRIES` with a corrective prompt; then heuristic fallback. |
| Missing API key | Full UI works via heuristics; banner explains how to add a key. |
| No KB matches | Informational empty state (scores below threshold). |
| Fewer than 2 docs | Conflict detection returns no findings. |

---

## License

Provided as-is for evaluation and internal use.
