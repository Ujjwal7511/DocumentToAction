# AGENT_USAGE.md — Agent & Tool Usage Log

This file documents how software agents (AI coding assistants, LLM sub-agents,
and CLI-driven automation) were used to build, extend, and verify the
**Document-to-Action Project Assistant**.

It covers the five things reviewers most often ask about:

1. **Tools used** and what they were used for
2. **Representative prompts** given to agents
3. **Work delegated to agents** (and where human review remains mandatory)
4. **Agent mistakes and rejected suggestions** (including the guardrails that caught them)
5. **How generated output was verified**

> Keep this file in sync with the repo. Whenever an agent contributes code,
> new prompts, or new automation, add a short note here.

---

## 1. Tools Used

Agents had access to a standard coding-assistant toolset. The table below
lists the tools actually used on this project and concrete examples of how
each one was applied.

| Tool | Purpose | Example usage on this project |
|------|---------|-------------------------------|
| `list_files` | Enumerate project structure | Recursively listed the repo to discover `app.py`, `db.py`, `extract.py`, `knowledge_base/`, `sample_docs/`, `tests/`. |
| `read_file` | Read source/context files | Read every core module (`app.py`, `auth.py`, `db.py`, `llm_client.py`, `ingest.py`, `extract.py`, `conflicts.py`, `kb.py`), the README/SPEC, sample docs, KB standards, and the pytest test. |
| `search_files` | Regex search across the codebase | Located prompt templates, the `approved`/`rejected` invariants, and `before_insert` guard logic across modules. |
| `execute_command` | Run CLI commands | Ran `python -m venv .venv`, `pip install -r requirements.txt`, and `git log --oneline` (confirmed the repo is not yet a git repository). |
| `create_file` | Create new files | Created project scaffolds and this documentation file. |
| `edit_file` | Apply surgical edits | Updated specific functions/guards during implementation (e.g., adding the `before_insert` approval guard in `db.py`). |
| `ask_followup_question` | Clarify intent | Confirmed scope decisions (e.g., which backends to support, whether MongoDB was in scope). |
| `attempt_completion` | Present finished work | Summarized completed milestones with run commands for the user. |
| LLM sub-agent | Structured generation | All `generate_json(prompt, schema)` calls in `ingest.py`, `extract.py`, and `conflicts.py` (see §3). |

---

## 2. Representative Prompts

Two kinds of prompts appear in this repo:

- **Agent meta-prompts** — the instructions an engineering agent receives to
  write or modify code (they are not stored in the repo).
- **Application prompts** — the prompts baked into the product that the LLM
  sub-agent receives at runtime. These ARE stored in the codebase.

### 2.1 Agent meta-prompts (examples)

Used while scaffolding and extending the project:

```
Build a Streamlit app that ingests up to three project documents (.txt/.md/.docx)
and produces a human-reviewed action summary. The AI must only PROPOSE action
items — never approve or assign them. Use a provider-agnostic LLM wrapper so the
LLM provider can be swapped via .env.
```

```
Replace the SQLAlchemy/SQLite persistence backend with MongoDB/PyMongo.
MongoDB is the only backend: MONGODB_URI is required (fail loudly if missing),
MONGODB_DB_NAME selects the database, and every editable collection stores
created_at/updated_at. Do not change the ActionItem approval contract: approved
must always default to False and only user-triggered methods may flip it.
```

```
Detect cross-document conflicts and repetitions across the extracted items.
Merge heuristic findings with LLM findings, and prefer "conflict" over
"repetition" when both fire for the same pair.
```

### 2.2 Application prompts (stored in the codebase)

These are the real runtime prompts. See the referenced source files for the
exact JSON schemas.

**Document-type detection** (`ingest.py::detect_document_type`):

```text
Classify the following project document into exactly one type:
meeting_notes, requirement_draft, implementation_notes, project_update,
decision_record, or unknown.

Filename: {filename}

Document:
---
{excerpt}
---

Return doc_type, confidence (0-1), and a short rationale.
```

**Structured extraction** (`extract.py::_call_extractor`) — key rules embedded
in the prompt:

```text
Rules:
1. item_type must be one of: fact, decision, assumption, risk, open_question, action_item
2. is_confirmed=true ONLY when the content is a direct statement in the text.
   is_confirmed=false when you are inferring or interpreting.
3. Include source_section matching a heading from the section index when possible.
4. Include a short source_quote copied from the document for offset linking.
5. For action_item entries, extract owner and deadline only if explicitly stated;
   otherwise leave them as empty strings. Do NOT invent owners or deadlines.
6. Do NOT assign or approve tasks — only propose action items that appear in
   or are clearly implied by the text.
7. If the document has little extractable content, return an empty items array.
```

**Gap suggestion** (`extract.py::suggest_missing_gaps`):

```text
Given these project documents and extracted items, suggest missing questions
or actions that are IMPLIED but not explicitly stated.

Examples of good gaps:
- A decision was recorded but no owner or deadline was assigned.
- An API change is planned but no rollback plan is mentioned.
- A risk is noted without a mitigation action.

Return gaps with content, rationale, and suggested_action.
Do NOT invent tasks that are unrelated. Do NOT mark anything as approved.
```

**Conflict / repetition detection** (`conflicts.py::_llm_conflicts`):

```text
Compare these extracted items from multiple project documents.
Identify:
1. conflicts — contradictory statements (e.g. different deadlines for the same task)
2. repetitions — the same fact/decision restated across documents

Use item_indices as 0-based indexes into the list below.
Only report genuine issues. Return an empty findings array if none.
```

---

## 3. Work Delegated to Agents

### 3.1 LLM sub-agent (runtime)

The application delegates the following to the configured LLM provider through
`llm_client.generate_json(prompt, schema)`:

| Task | Module / function | Constraint enforced |
|------|-------------------|---------------------|
| Document-type detection | `ingest.py::detect_document_type` | Returns one enum value + confidence + rationale; falls back to keyword heuristics. |
| Structured item extraction | `extract.py::_call_extractor` | `is_confirmed` must reflect direct vs. inferred; owner/deadline only when stated; **never approves tasks**. |
| Gap suggestion | `extract.py::suggest_missing_gaps` | Suggestions are stored as `suggested_gap` items with `is_confirmed=False`; generated actions are proposals only. |
| Cross-document conflict/repetition detection | `conflicts.py::_llm_conflicts` | Reports 0-based item indices; only genuine issues; empty array allowed. |

The LLM sub-agent is treated as **non-authoritative**: every output is persisted
as a proposal or as flagged interpreted content, and a human reviews it in the
Streamlit UI.

### 3.2 Tool-delegated subtasks

- **Scaffolding & edits** — `create_file` / `edit_file` performed the bulk of
  file generation and surgical updates.
- **CLI automation** — `execute_command` handled environment setup
  (`python -m venv .venv`, `pip install -r requirements.txt`) and repo checks.
- **Code inspection** — `list_files`, `read_file`, and `search_files` were used
  to keep edits consistent with existing patterns (e.g., matching the CRUD
  style when adding the MongoDB backend).

### 3.3 What is NOT delegated (human-only)

The product's core guarantee: **no agent may approve or assign work.**

- Approval / rejection of action items is only possible via the UI buttons
  (`app.py::render_action_items`) or the reserved CRUD methods
  `approve_action_item()` / `reject_action_item()`.
- Conflict resolution status (`open` / `resolved` / `annotated`) is a
  human decision.
- Saving the final reviewed summary is an explicit human action.

---

## 4. Agent Mistakes and Rejected Suggestions

### 4.1 Mistakes caught by guardrails

| Incident | How it was caught / handled |
|----------|-----------------------------|
| LLM returned malformed JSON or markdown-fenced JSON | `llm_client.py` retries up to `LLM_MAX_RETRIES` (default 3) with a corrective nudge: `"Previous response was invalid JSON ({exc}). Return corrected JSON only."` Then `_parse_json_strict` strips code fences. |
| LLM returned an empty response | Treated as `ValueError("Empty response from Gemini")`, triggering the same retry path. |
| LLM provider unset / quota exhausted | `is_configured()` returns `False` → code paths fall back to heuristic extraction, type detection, and conflict detection so the demo still works. |
| A pipeline path attempted to create an approved action item | Two layers block it: (a) `ActionItem.approved` defaults `False` and `db.add_action_item()` always writes `approved=False`, `rejected=False`, `status="proposed"` on every insert (the proposal-only contract in `db.py`); (b) `extract.py::run_full_extraction` runs an invariant check after extraction and resets any action found approved. |
| Action item accidentally approved by pipeline | `extract.py::run_full_extraction` runs an invariant check after extraction; if any `ActionItem.approved` is found, it logs `INVARIANT VIOLATION` and resets the row back to `proposed`. |
| LLM type detection returned an out-of-enum value | `ingest.py` coerces unknown values back to `"unknown"`. |
| LLM conflict detection referenced out-of-range indices | `conflicts.py` validates indices and drops findings with fewer than 2 valid item ids. |
| LLM extraction returned a non-list `items` payload | `extract.py::_call_extractor` falls back to heuristic extraction. |
| LLM suggested fabricated owners/deadlines | Prompt rules forbid inventing them, and `extract.py` only persists owner/deadline that were explicitly extracted. |
| Overly noisy heuristic gaps | `_heuristic_gaps` caps output at 8 items and deduplicates by content prefix. |

### 4.2 Rejected or overruled suggestions

These are decisions where an alternative approach was considered and explicitly
**rejected** (documented in `sample_docs/03_decision_record_jwt.md` and the
codebase design):

| Alternative | Why it was rejected |
|-------------|---------------------|
| Opaque server-side tokens in Redis for partner auth | Rejected due to cross-region latency; JWT bearer tokens chosen instead. |
| Mutual TLS only for partner auth | Rejected as too heavy for mid-market partners. |
| Vector database for the knowledge base | Rejected in favor of TF-IDF + cosine similarity (`kb.py`), which is appropriate for a small curated corpus and avoids an extra dependency. |
| Auto-assigning/approving action items during extraction | **Rejected by design** — the entire product exists to keep task assignment human-reviewed. |
| Keeping cookie sessions indefinitely | Rejected in the ADR; legacy cookie sessions are kept only as a short-term fallback. |

### 4.3 Known intentional behavior worth noting

- The bundled sample documents intentionally disagree on the partner migration
  deadline (**March 18 / 20 / 28**) so conflict detection has real content to
  surface during a demo. This is a feature, not a bug.

---

## 5. Verification of Generated Output

### 5.1 Automated tests

- `tests/test_db_backend.py` verifies the MongoDB-only backend: the backend name
  is `mongodb`, `MONGODB_URI` is required, and session/document/action CRUD
  round-trips through PyMongo (create, read, edit, approve, reject, reset):

  ```bash
  pytest tests/
  ```

- `tests/test_auth.py` verifies signup/login against the MongoDB `users`
  collection (no local `users.json` file is used).

### 5.2 Approval-invariant verification (the critical guarantee)

The "never auto-approve" property is verified at multiple layers:

1. **Schema default** — `add_action_item()` always writes `approved=False`, `status = "proposed"`.
2. **Write path** — `db.add_action_item()` enforces the proposal-only contract on every insert.
3. **Write path** — `extract.py` only calls `db.add_action_item()`, which is documented as proposal-only.
4. **Approval API** — only `approve_action_item()` / UI **Approve** buttons flip `approved` to `True`.
5. **Summary filter** — `db.build_reviewed_summary_payload()` includes actions only when `approved and not rejected`, and `extract.py::run_full_extraction` audits all actions post-extraction.

### 5.3 Manual / demo verification

The README documents a full heuristic-mode walkthrough that exercises every
stage of the pipeline without an API key:

1. **Load sample documents** (loads the three files in `sample_docs/`).
2. **Run analysis** — triggers ingest → extraction → conflict detection → KB matching.
3. **Review each document tab** — verify Confirmed/Interpreted pills, source sections, and character offsets; edit items; set classifications.
4. **Resolve the intentional deadline conflict** (March 18 / 20 / 28) — confirm the conflict appears under the **Conflicts** tab.
5. **Approve or reject proposed actions** — confirm actions only move to approved/rejected via explicit user action.
6. **Open Review Summary** — confirm only approved actions, resolved/annotated conflicts, and non-unresolved items appear in the saved payload.

### 5.4 Output-quality checks baked into the pipeline

- **Source-offset linking** — every extracted item carries `source_section` +
  character offsets; `extract.py::_find_offsets` matches exact → case-insensitive →
  prefix → section fallback, and the UI renders an excerpt around the offsets so
  a reviewer can jump back to the evidence.
- **Malformed JSON handling** — verified by the retry-with-correction logic in
  `llm_client.py` and the heuristic fallback if the LLM still fails.
- **Deduplication** — conflict findings are deduplicated by item-id set;
  gap suggestions are deduplicated by content prefix.
- **Empty/invalid inputs** — empty documents skip extraction, fewer than 2 docs
  skip conflict detection, no KB hits show an informational empty state, and
  uploads are validated (≤ 3 files, allowed extensions) before analysis.

---

## Appendix — How to reproduce the agent environment

```bash
# One-time setup
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

# Run the app (heuristic mode works without an API key)
streamlit run app.py

# Run tests
pytest tests/
```

