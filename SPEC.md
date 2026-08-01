# Document-to-Action Project Assistant — Specification

## Purpose

Accept up to three project documents (`.txt`, `.md`, `.docx`), run an AI analysis workflow, and produce a human-reviewed, editable action summary. The AI **proposes** only; humans **approve**.

## Non-negotiable constraint

Action items are always created with `approved=False`. The extraction pipeline must never set `approved=True`. Only an explicit user action (UI button / CRUD method reserved for approval) may flip that flag.

## Document types

| Type | Description |
|------|-------------|
| `meeting_notes` | Notes from meetings, standups, syncs |
| `requirement_draft` | Product/engineering requirements |
| `implementation_notes` | Tech design, implementation details |
| `project_update` | Status reports, progress updates |
| `decision_record` | ADRs / decision logs |

## Extracted item categories

- Confirmed facts
- Decisions
- Assumptions
- Risks
- Open questions
- Proposed action items

Every item carries:

- `is_confirmed` — `True` if directly stated in source; `False` if inferred
- Source document id + section heading + character offsets
- Optional user classification override: `fact` | `assumption` | `unresolved`

## Workflow stages

1. **Ingest** — parse files, detect document type
2. **Extract** — LLM structured extraction with source links
3. **Conflicts** — cross-document repetition / contradiction detection
4. **KB match** — TF-IDF retrieval of relevant org standards
5. **Suggest gaps** — missing owners, deadlines, unanswered implications
6. **Human review** — edit, classify, approve/reject actions, resolve conflicts
7. **Save summary** — persist only approved/resolved content

## Tech stack

Python · Google Gemini (`google-genai`) · MongoDB (PyMongo) · scikit-learn TF-IDF · python-docx · Streamlit
