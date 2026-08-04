"""Document-to-Action Project Assistant — Streamlit UI.

Human review is mandatory: action items stay proposed until the user
explicitly approves or rejects them. The extraction pipeline never assigns
or approves work.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import streamlit as st

import auth
import conflicts as conflicts_mod
import db
import extract
import ingest
import kb
import llm_client

ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = ROOT / "sample_docs"

DOC_TYPE_LABELS = {
    "meeting_notes": "Meeting notes",
    "requirement_draft": "Requirement draft",
    "implementation_notes": "Implementation notes",
    "project_update": "Project update",
    "decision_record": "Decision record",
    "unknown": "Unknown",
}

ITEM_TYPE_LABELS = {
    "fact": "Fact",
    "decision": "Decision",
    "assumption": "Assumption",
    "risk": "Risk",
    "open_question": "Open question",
    "action_item": "Action item",
    "suggested_gap": "Suggested gap",
}

CLASSIFICATION_OPTIONS = ["", "fact", "assumption", "unresolved"]


# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

        html, body, [class*="css"] {
            font-family: 'IBM Plex Sans', sans-serif;
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
            max-width: 1100px;
        }
        h1, h2, h3 {
            font-family: 'DM Sans', sans-serif !important;
            letter-spacing: -0.02em;
        }
        .dta-hero {
            background: linear-gradient(135deg, #0f2b24 0%, #1a4a3c 48%, #243b55 100%);
            color: #f4f7f5;
            padding: 1.75rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            border: 1px solid rgba(255,255,255,0.08);
        }
        .dta-hero h1 {
            color: #f4f7f5 !important;
            margin: 0 0 0.35rem 0;
            font-size: 1.85rem;
        }
        .dta-hero p {
            margin: 0;
            color: #c5d5ce;
            font-size: 0.98rem;
            max-width: 42rem;
        }
        .dta-badge {
            display: inline-block;
            background: rgba(110, 200, 160, 0.18);
            color: #9ee0bc;
            border: 1px solid rgba(110, 200, 160, 0.35);
            padding: 0.15rem 0.55rem;
            border-radius: 4px;
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            margin-bottom: 0.65rem;
        }
        .dta-card {
            background: #f7faf8;
            border: 1px solid #d7e3dc;
            border-radius: 10px;
            padding: 1rem 1.15rem;
            margin-bottom: 0.75rem;
        }
        .dta-muted { color: #5c6f66; font-size: 0.9rem; }
        .dta-pill-confirmed {
            background: #e4f5ea; color: #1d6b3f; padding: 0.1rem 0.45rem;
            border-radius: 4px; font-size: 0.75rem; font-weight: 600;
        }
        .dta-pill-interpreted {
            background: #fff3df; color: #8a5a10; padding: 0.1rem 0.45rem;
            border-radius: 4px; font-size: 0.75rem; font-weight: 600;
        }
        .dta-pill-proposed {
            background: #e8eef8; color: #2c4a7c; padding: 0.1rem 0.45rem;
            border-radius: 4px; font-size: 0.75rem; font-weight: 600;
        }
        .dta-pill-approved {
            background: #e4f5ea; color: #1d6b3f; padding: 0.1rem 0.45rem;
            border-radius: 4px; font-size: 0.75rem; font-weight: 600;
        }
        .dta-pill-rejected {
            background: #fde8e6; color: #8f2f28; padding: 0.1rem 0.45rem;
            border-radius: 4px; font-size: 0.75rem; font-weight: 600;
        }
        .dta-metric-card {
            background: linear-gradient(135deg, #f7faf8 0%, #eef5ef 100%);
            border: 1px solid #d7e3dc;
            border-radius: 12px;
            padding: 0.9rem 1rem;
            margin-bottom: 0.5rem;
            min-height: 84px;
            color: #0f2b24;
        }
        .dta-metric-label {
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: #5b756d;
            margin-bottom: 0.35rem;
        }
        .dta-metric-value {
            font-size: 1.2rem;
            font-weight: 700;
            color: #0f2b24;
        }
        .dta-auth-shell {
            min-height: 72vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1rem 0 2rem;
        }
        .dta-header-controls {
            height: 0.45rem;
        }
        .dta-auth-card {
            max-width: 430px;
            width: 100%;
            margin: 0 auto;
            padding: 1.4rem 1.35rem;
            border-radius: 16px;
            background: #ffffff;
            border: 1px solid #d7e3dc;
            box-shadow: 0 16px 40px rgba(15, 43, 36, 0.08);
        }
        div[data-testid="stMetric"] {
            background: #f7faf8;
            border: 1px solid #d7e3dc;
            border-radius: 10px;
            padding: 0.75rem 1rem;
        }
        .stTabs [data-baseweb="tab-list"] { gap: 0.35rem; }
        .stTabs [data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            padding: 0.5rem 1rem;
            font-weight: 500;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def _initialize_database() -> str:
    """Run the MongoDB ping/index setup once per Streamlit server process."""
    db.init_db()
    return db.get_backend_name()


def _init_state() -> None:
    _initialize_database()
    defaults = {
        "session_id": None,
        "analysis_done": False,
        "last_error": None,
        "status_message": None,
        "authenticated": False,
        "project_title": "Partner API Auth Migration",
        "prepared_uploads": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _logout_user() -> None:
    for key in [
        "authenticated",
        "session_id",
        "analysis_done",
        "last_error",
        "status_message",
        "project_title",
        "prepared_uploads",
        "login_username",
        "login_password",
        "signup_username",
        "signup_password",
        "signup_confirm",
    ]:
        st.session_state.pop(key, None)
    st.session_state.authenticated = False
    st.session_state.session_id = None
    st.session_state.analysis_done = False
    st.session_state.last_error = None
    st.session_state.status_message = None
    st.session_state.project_title = "Partner API Auth Migration"
    st.session_state.prepared_uploads = None
    st.rerun()


def _evidence_excerpt(document_id: Optional[int], start: int, end: int) -> str:
    if not document_id:
        return ""
    doc = db.get_document(document_id)
    if not doc or not doc.get("content"):
        return ""
    text = doc.get("content") or ""
    start = max(0, min(start, len(text)))
    end = max(start, min(end if end > start else start + 160, len(text)))
    # Expand slightly for readability
    window_start = max(0, start - 40)
    window_end = min(len(text), end + 40)
    excerpt = text[window_start:window_end]
    if window_start > 0:
        excerpt = "…" + excerpt
    if window_end < len(text):
        excerpt = excerpt + "…"
    return excerpt


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------


def run_analysis(uploaded_files: list, title: str) -> None:
    """Ingest uploads, extract, detect conflicts, match KB."""
    error = ingest.validate_upload_batch([f.name for f in uploaded_files])
    if error:
        st.session_state.last_error = error
        return

    sess = db.create_session(title=title or "Untitled Project")
    st.session_state.session_id = sess["id"]
    st.session_state.analysis_done = False
    st.session_state.last_error = None

    progress = st.progress(0, text="Ingesting documents…")
    n = len(uploaded_files)

    try:
        parsed_uploads = ingest.ingest_uploads_parallel(
            [(uf.name, uf.getvalue()) for uf in uploaded_files]
        )
    except ValueError as exc:
        st.session_state.last_error = str(exc)
        return

    for i, parsed in enumerate(parsed_uploads):
        db.add_document(
            session_id=sess["id"],
            filename=parsed["filename"],
            content=parsed["content"],
            doc_type=parsed["doc_type"],
            doc_type_confidence=parsed["doc_type_confidence"],
        )
        progress.progress((i + 1) / (n + 3), text=f"Ingested {parsed['filename']}")

    progress.progress(0.55, text="Extracting facts, decisions, risks, and proposed actions…")
    extract.run_full_extraction(sess["id"])

    progress.progress(0.75, text="Detecting cross-document conflicts…")
    conflicts_mod.detect_conflicts(sess["id"])

    progress.progress(0.9, text="Matching organizational standards…")
    kb.match_session_to_kb(sess["id"])

    progress.progress(1.0, text="Analysis complete")
    st.session_state.analysis_done = True
    st.session_state.status_message = (
        "Analysis complete. Review extracted items and approve actions individually — "
        "nothing is assigned automatically."
    )


def load_sample_docs() -> list[tuple[str, bytes]]:
    """Load bundled sample documents for the demo path."""
    files = []
    if not SAMPLE_DIR.exists():
        return files
    for path in sorted(SAMPLE_DIR.iterdir()):
        if path.suffix.lower() in ingest.ALLOWED_EXTENSIONS:
            files.append((path.name, path.read_bytes()))
    return files


class _FakeUpload:
    """Minimal file-like object matching Streamlit UploadedFile for samples."""

    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


# ---------------------------------------------------------------------------
# UI sections
# ---------------------------------------------------------------------------


def render_auth_panel() -> None:
    if st.session_state.get("authenticated", False):
        return

    _, center, _ = st.columns([1, 2.2, 1], vertical_alignment="center")
    with center:
        with st.container(border=True):
            st.markdown(
                '<span class="dta-badge">Secure workspace access</span>',
                unsafe_allow_html=True,
            )
            st.markdown("<h2 style='margin: 0.2rem 0 0.3rem 0;'>Welcome back</h2>", unsafe_allow_html=True)
            st.markdown(
                "<p class='dta-muted' style='margin: 0;'>Sign in to review documents and approve actions.</p>",
                unsafe_allow_html=True,
            )

            login_tab, signup_tab = st.tabs(["Login", "Sign up"])

            with login_tab:
                username = st.text_input("Username", key="login_username")
                password = st.text_input("Password", type="password", key="login_password")
                if st.button("Continue", key="login_submit"):
                    ok, msg = auth.login_user(username, password)
                    if ok:
                        st.session_state.authenticated = True
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

            with signup_tab:
                username = st.text_input("Choose username", key="signup_username")
                password = st.text_input("Choose password", type="password", key="signup_password")
                confirm_password = st.text_input("Confirm password", type="password", key="signup_confirm")
                if st.button("Create account", key="signup_submit"):
                    if password != confirm_password:
                        st.error("Passwords do not match.")
                    else:
                        ok, msg = auth.create_account(username, password)
                        st.success(msg) if ok else st.error(msg)
                        if ok:
                            st.rerun()

            st.caption("Demo login: username demo, password demo123")
    st.stop()


def render_hero() -> None:
    configured = llm_client.is_configured()
    provider = llm_client.provider_name()
    status = (
        f"LLM ready · {provider}"
        if configured
        else "LLM key missing · heuristic mode active"
    )
    if st.session_state.get("authenticated", False):
        with st.container(horizontal=True, horizontal_alignment="right"):
            if st.button("History", icon=":material/history:", key="history_toggle"):
                st.session_state.show_history = not st.session_state.get("show_history", False)
            if st.button("Logout", icon=":material/logout:", key="global_logout"):
                _logout_user()
    st.markdown(
        f"""
        <div class="dta-hero">
          <div class="dta-badge">{status}</div>
          <h1>Document-to-Action</h1>
          <p>
            Upload up to three project documents. The assistant extracts facts,
            decisions, risks, and <em>proposed</em> action items — then you review,
            edit, and approve. Nothing is assigned without your say-so.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_upload_panel() -> None:
    st.subheader("1. Upload documents")
    st.caption("Accepted formats: .txt, .md, .markdown, .docx, .pdf · Maximum 3 files")

    col_a, col_b = st.columns([2, 1])
    with col_a:
        title = st.text_input(
            "Project title",
            value=st.session_state.get("project_title", "Partner API Auth Migration"),
            help="Used to label this analysis session.",
        )
        st.session_state.project_title = title or "Untitled Project"
    with col_b:
        use_samples = st.button("Load sample documents", use_container_width=True)

    uploads = st.file_uploader(
        "Project documents",
        type=["txt", "md", "markdown", "docx", "pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if uploads:
        st.session_state.prepared_uploads = list(uploads)
    elif st.session_state.get("prepared_uploads") is not None and not use_samples:
        uploads = list(st.session_state.prepared_uploads)

    if use_samples:
        samples = load_sample_docs()
        if not samples:
            st.warning("No sample documents found in sample_docs/.")
        else:
            st.session_state.prepared_uploads = [_FakeUpload(n, d) for n, d in samples]
            uploads = list(st.session_state.prepared_uploads)
            st.info(f"Loaded {len(uploads)} sample documents. Click **Run analysis** to continue.")

    if uploads and len(uploads) > 3:
        st.error("Please upload at most 3 documents.")
        return

    if uploads:
        st.caption(f"{len(uploads)} file(s) selected")

    run = st.button(
        "Run analysis",
        type="primary",
        disabled=not uploads,
        use_container_width=False,
    )
    if run and uploads:
        with st.spinner("Running AI workflow…"):
            run_analysis(list(uploads)[:3], title)
        st.rerun()

    if st.session_state.last_error:
        st.error(st.session_state.last_error)
    if st.session_state.status_message and st.session_state.analysis_done:
        st.success(st.session_state.status_message)


def render_history_panel() -> None:
    """Expose MongoDB-backed project sessions and let the user reopen one."""
    if not st.session_state.get("show_history", False):
        return

    sessions = db.list_project_sessions()
    with st.container(border=True):
        st.markdown("#### Project history")
        if not sessions:
            st.info("No saved projects yet. Run an analysis to create one.")
            return
        for project in sessions:
            created = project.get("created_at")
            timestamp = created.strftime("%Y-%m-%d %H:%M") if created else "Unknown date"
            col_a, col_b = st.columns([4, 1])
            with col_a:
                st.markdown(f"**{project.get('title')}**  \\n{timestamp}")
            with col_b:
                if st.button("Open", key=f"open_history_{project['id']}"):
                    st.session_state.session_id = project["id"]
                    st.session_state.project_title = project.get("title") or "Untitled Project"
                    st.session_state.analysis_done = True
                    st.session_state.prepared_uploads = None
                    st.session_state.status_message = f"Opened saved project: {project.get('title')}"
                    st.rerun()


def _render_metric_card(label: str, value: Any) -> None:
    st.markdown(
        f"<div class='dta-metric-card'><div class='dta-metric-label'>{label}</div><div class='dta-metric-value'>{value}</div></div>",
        unsafe_allow_html=True,
    )


def render_session_metrics(session_id: int) -> None:
    docs = db.list_documents(session_id)
    items = db.list_extracted_items(session_id=session_id)
    actions = db.list_action_items(session_id)
    confs = db.list_conflicts(session_id)
    approved = sum(1 for a in actions if a.get("approved"))
    proposed = sum(
        1 for a in actions if str(a.get("status", "")).lower() == "proposed"
    )

    cols = st.columns(5)
    values = [
        ("Documents", len(docs)),
        ("Extracted items", len(items)),
        ("Conflicts", len(confs)),
        ("Proposed actions", proposed),
        ("Approved actions", approved),
    ]
    for col, (label, value) in zip(cols, values):
        with col:
            _render_metric_card(label, value)


def _items_dataframe(items: list[dict], docs_by_id: dict[int, dict]) -> pd.DataFrame:
    rows = []
    for it in items:
        doc = docs_by_id.get(it.get("document_id"))
        rows.append(
            {
                "id": it.get("id"),
                "type": it.get("item_type"),
                "content": it.get("content"),
                "origin": "Confirmed" if it.get("is_confirmed") else "Interpreted",
                "confidence": round(float(it.get("confidence", 0.0)), 2),
                "classification": it.get("user_classification") or "",
                "source_doc": doc.get("filename") if doc else "",
                "section": it.get("source_section"),
                "offset_start": it.get("source_offset_start"),
                "offset_end": it.get("source_offset_end"),
            }
        )
    return pd.DataFrame(rows)


def render_document_tab(doc: dict) -> None:
    doc_id = doc.get("id")
    st.markdown(
        f"**Type:** {DOC_TYPE_LABELS.get(doc.get('doc_type'), doc.get('doc_type'))} "
        f"· confidence {float(doc.get('doc_type_confidence', 0.0)):.0%}"
    )

    with st.expander("View source document", expanded=False):
        if not (doc.get("content") or "").strip():
            st.warning("This document is empty — nothing to extract.")
        else:
            st.text(doc.get("content"))

    items = [
        i
        for i in db.list_extracted_items(document_id=doc_id)
        if i.get("item_type") != "action_item"  # actions reviewed in dedicated section
    ]

    if not items:
        st.info("No extractable items found in this document.")
        return

    docs_by_id = {doc_id: doc}
    df = _items_dataframe(items, docs_by_id)

    st.caption(
        "Edit content or set classification (fact / assumption / unresolved). "
        "Origin shows whether the model treated the item as a direct statement or an inference."
    )

    edited = st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "type": st.column_config.TextColumn("Type", disabled=True, width="small"),
            "content": st.column_config.TextColumn("Content", width="large"),
            "origin": st.column_config.TextColumn("Origin", disabled=True, width="small"),
            "confidence": st.column_config.NumberColumn("Conf.", disabled=True, width="small"),
            "classification": st.column_config.SelectboxColumn(
                "Mark as",
                options=CLASSIFICATION_OPTIONS,
                required=False,
                width="small",
            ),
            "source_doc": st.column_config.TextColumn("Source doc", disabled=True),
            "section": st.column_config.TextColumn("Section", disabled=True),
            "offset_start": None,
            "offset_end": None,
        },
        key=f"editor_doc_{doc_id}",
    )

    if st.button("Save edits", key=f"save_doc_{doc_id}"):
        failed = []
        for _, row in edited.iterrows():
            ok = db.update_extracted_item(
                int(row["id"]),
                content=str(row["content"]),
                user_classification=str(row["classification"]) or None,
            )
            if not ok:
                failed.append(int(row["id"]))
        if failed:
            st.error(f"Could not update item(s): {failed}")
        else:
            st.success("Saved item edits.")
        st.rerun()

    st.markdown("##### Source evidence")
    for it in items:
        label = (
            f"{ITEM_TYPE_LABELS.get(it.get('item_type'), it.get('item_type'))} — "
            f"{(it.get('content') or '')[:70]}"
        )
        with st.expander(label):
            origin = (
                '<span class="dta-pill-confirmed">Confirmed (direct)</span>'
                if it.get("is_confirmed")
                else '<span class="dta-pill-interpreted">Interpreted</span>'
            )
            st.markdown(origin, unsafe_allow_html=True)
            st.markdown(f"**Section:** {it.get('source_section') or '—'}")
            st.markdown(
                f"**Offsets:** {it.get('source_offset_start')}–{it.get('source_offset_end')}"
            )
            excerpt = _evidence_excerpt(
                doc_id, it.get("source_offset_start"), it.get("source_offset_end")
            )
            if excerpt:
                st.code(excerpt, language=None)
            else:
                st.caption("No source excerpt available.")


def render_conflicts(session_id: int) -> None:
    st.subheader("Cross-document conflicts & repetitions")
    conf_list = db.list_conflicts(session_id)
    if not conf_list:
        st.info("No conflicts or repetitions detected across the uploaded documents.")
        return

    for c in conf_list:
        detail = conflicts_mod.format_conflict_detail(c)
        icon = "!" if c.get("conflict_type") == "conflict" else "="
        with st.expander(
            f"{icon} [{c.get('conflict_type')}] {(c.get('description') or '')[:110]} · {c.get('status')}",
            expanded=c.get("status") == "open",
        ):
            st.write(c.get("description"))
            for item in detail["items"]:
                origin = "Confirmed" if item["is_confirmed"] else "Interpreted"
                st.markdown(
                    f"- **{item['filename']}** / {item['source_section']} "
                    f"({item['item_type']}, {origin}): {item['content']}"
                )
                excerpt = _evidence_excerpt(
                    item["document_id"], item["offset_start"], item["offset_end"]
                )
                if excerpt:
                    st.code(excerpt, language=None)

            note = st.text_area(
                "Resolution / annotation",
                value=c.get("resolution_note") or "",
                key=f"conflict_note_{c.get('id')}",
            )
            b1, b2, b3 = st.columns(3)
            if b1.button("Mark resolved", key=f"resolve_{c.get('id')}"):
                db.resolve_conflict(c.get("id"), "resolved", note)
                st.rerun()
            if b2.button("Annotate only", key=f"annotate_{c.get('id')}"):
                db.resolve_conflict(c.get("id"), "annotated", note)
                st.rerun()
            if b3.button("Reopen", key=f"reopen_{c.get('id')}"):
                db.resolve_conflict(c.get("id"), "open", note)
                st.rerun()


def _clean_kb_snippet(snippet: str) -> str:
    cleaned = re.sub(r"^#{1,6}\s*", "", snippet, flags=re.MULTILINE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 260:
        cleaned = cleaned[:257].rstrip() + "..."
    return cleaned


def render_kb_matches(session_id: int) -> None:
    st.subheader("Relevant organizational standards")
    matches = db.list_kb_matches(session_id)
    if not matches:
        st.info(
            "No knowledge-base standards scored above the relevance threshold "
            "for this session."
        )
        return

    for m in matches:
        with st.expander(f"{m.get('kb_title')} · score {float(m.get('relevance_score', 0.0)):.3f}"):
            st.caption(m.get("kb_filename"))
            st.write(_clean_kb_snippet(m.get("snippet") or "") or "No excerpt available.")
            if m.get("rationale"):
                st.caption(m.get("rationale"))


def _format_last_updated(updated_at: Any) -> str:
    if not updated_at:
        return "Last updated: not yet saved"
    if hasattr(updated_at, "strftime"):
        return f"Last updated: {updated_at.strftime('%Y-%m-%d %H:%M')}"
    return f"Last updated: {updated_at}"


def render_action_items(session_id: int) -> None:
    st.subheader("Action items")
    st.caption(
        "All actions start as **proposed**. Approve or reject each one individually. "
        "Rejected items remain visible here but are excluded from the Review Summary."
    )
    actions = db.list_action_items(session_id)
    if not actions:
        st.info("No action items were proposed from these documents.")
        return

    filter_status = st.radio(
        "Show status",
        options=["All", "Proposed", "Approved", "Rejected"],
        index=0,
        horizontal=True,
        key=f"action_status_filter_{session_id}",
    )
    normalized_filter = (filter_status or "All").lower()
    filtered_actions = []
    for action in actions:
        status = str(action.get("status") or "").lower()
        if normalized_filter == "all" or status == normalized_filter:
            filtered_actions.append(action)

    if not filtered_actions:
        st.info(f"No {filter_status.lower()} action items found.")
        return

    for a in filtered_actions:
        status_html = {
            "proposed": '<span class="dta-pill-proposed">Proposed</span>',
            "approved": '<span class="dta-pill-approved">Approved</span>',
            "rejected": '<span class="dta-pill-rejected">Rejected</span>',
        }.get(a.get("status"), a.get("status"))
        origin = (
            '<span class="dta-pill-confirmed">Confirmed</span>'
            if a.get("is_confirmed")
            else '<span class="dta-pill-interpreted">Interpreted</span>'
        )

        action_id = a.get("id")
        with st.container():
            st.markdown(
                f"**#{action_id}** {status_html} {origin}",
                unsafe_allow_html=True,
            )
            st.caption(_format_last_updated(a.get("updated_at")))
            desc = st.text_area(
                "Description",
                value=a.get("description") or "",
                key=f"action_desc_{action_id}",
                label_visibility="collapsed",
            )
            c1, c2, c3 = st.columns(3)
            owner = c1.text_input(
                "Owner", value=a.get("owner") or "", key=f"action_owner_{action_id}"
            )
            deadline = c2.text_input(
                "Deadline", value=a.get("deadline") or "", key=f"action_deadline_{action_id}"
            )
            c3.markdown(
                f"<div class='dta-muted'>Section: {a.get('source_section') or '—'}</div>",
                unsafe_allow_html=True,
            )

            if a.get("source_document_id"):
                with st.expander("Source evidence", expanded=False):
                    item = (
                        db.get_extracted_item(a.get("extracted_item_id"))
                        if a.get("extracted_item_id")
                        else None
                    )
                    if item:
                        excerpt = _evidence_excerpt(
                            item.get("document_id"),
                            item.get("source_offset_start"),
                            item.get("source_offset_end"),
                        )
                        st.code(excerpt or "(no excerpt)", language=None)
                    else:
                        doc = db.get_document(a.get("source_document_id"))
                        st.caption(doc.get("filename") if doc else "Unknown document")

            b1, b2, b3, b4 = st.columns(4)
            if b1.button("Save edits", key=f"action_save_{action_id}"):
                ok = db.update_action_item(
                    action_id,
                    description=desc,
                    owner=owner or None,
                    deadline=deadline or None,
                )
                if ok:
                    st.toast(f"Saved action #{action_id}")
                else:
                    st.error(f"Unable to save action #{action_id}: no matching record found.")
                st.rerun()
            if b2.button("Approve", key=f"action_approve_{action_id}", type="primary"):
                updated = db.update_action_item(
                    action_id,
                    description=desc,
                    owner=owner or None,
                    deadline=deadline or None,
                )
                approved = updated and db.approve_action_item(action_id)
                if approved:
                    st.toast(f"Approved action #{action_id}")
                else:
                    st.error(f"Unable to approve action #{action_id}: no matching record found.")
                st.rerun()
            if b3.button("Reject", key=f"action_reject_{action_id}"):
                rejected = db.reject_action_item(action_id)
                if rejected:
                    st.toast(f"Rejected action #{action_id}")
                else:
                    st.error(f"Unable to reject action #{action_id}: no matching record found.")
                st.rerun()
            if b4.button("Reset to proposed", key=f"action_reset_{action_id}"):
                reset = db.reset_action_to_proposed(action_id)
                if reset:
                    st.toast(f"Reset action #{action_id} to proposed")
                else:
                    st.error(f"Unable to reset action #{action_id}: no matching record found.")
                st.rerun()
            st.divider()


def _build_review_summary_markdown(payload: dict[str, Any], project_title: str) -> str:
    title = (project_title or "Project Review").strip() or "Project Review"
    docs = {d.get("id"): d for d in payload.get("documents", [])}
    lines: list[str] = [f"# {title} - Review Summary", "", "This summary includes approved actions, resolved or annotated conflicts, and reviewed extracted items.", ""]

    extracted_items = payload.get("extracted_items", [])
    if extracted_items:
        lines.append("## Reviewed Items")
        lines.append("")
        for item in extracted_items:
            item_type = str(item.get("type", "item")).replace("_", " ").title()
            source_doc = docs.get(item.get("source_document_id")) or {}
            source_name = source_doc.get("filename") or "Unknown source"
            section = item.get("source_section") or "—"
            lines.append(f"- **{item_type}**: {item.get('content', '')}")
            lines.append(f"  - Source: {source_name}")
            lines.append(f"  - Section: {section}")
        lines.append("")

    approved_actions = payload.get("approved_actions", [])
    if approved_actions:
        lines.append("## Approved Action Items")
        lines.append("")
        for action in approved_actions:
            owner = action.get("owner") or "Unassigned"
            deadline = action.get("deadline") or "No deadline"
            lines.append(f"- **{action.get('description', '')}**")
            lines.append(f"  - Owner: {owner}")
            lines.append(f"  - Deadline: {deadline}")
        lines.append("")

    resolved_conflicts = payload.get("resolved_conflicts", [])
    if resolved_conflicts:
        lines.append("## Resolved Conflicts")
        lines.append("")
        for conflict in resolved_conflicts:
            lines.append(f"- **{conflict.get('type', 'conflict').title()}**: {conflict.get('description', '')}")
            if conflict.get("resolution_note"):
                lines.append(f"  - Resolution: {conflict.get('resolution_note')}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def render_review_summary(session_id: int) -> None:
    st.subheader("Review summary")
    st.caption(
        "The saved summary includes approved actions, resolved/annotated conflicts, "
        "and extracted items that are not marked unresolved. Proposed (unapproved) "
        "actions are excluded."
    )

    payload = db.build_reviewed_summary_payload(session_id)
    project_title = st.session_state.get("project_title", "Project Review")
    markdown_summary = _build_review_summary_markdown(payload, project_title)

    c1, c2, c3 = st.columns(3)
    with c1:
        _render_metric_card("Items included", len(payload.get("extracted_items", [])))
    with c2:
        _render_metric_card("Approved actions", len(payload.get("approved_actions", [])))
    with c3:
        _render_metric_card("Resolved conflicts", len(payload.get("resolved_conflicts", [])))

    if st.button("Save reviewed project action summary", type="primary"):
        if not payload.get("approved_actions") and not payload.get("extracted_items"):
            st.warning(
                "Nothing to save yet — approve at least one action or keep some "
                "extracted items that are not marked unresolved."
            )
        else:
            summary = db.save_reviewed_summary(
                session_id,
                title="Reviewed Project Action Summary",
                content=payload,
            )
            st.success(f"Summary saved (id={summary['id']}).")
            st.rerun()

    file_name = f"{re.sub(r'[^A-Za-z0-9._-]+', '_', project_title).strip('_') or 'review_summary'}_review_summary.md"
    st.download_button(
        "Download Review Summary",
        data=markdown_summary,
        file_name=file_name,
        mime="text/markdown",
    )

    summaries = db.list_summaries(session_id)
    if not summaries:
        st.info("No saved summaries yet.")
        return

    for s in summaries:
        with st.expander(f"{s.get('title')} · {s.get('created_at')}", expanded=False):
            content = s.get("content") or {}
            st.markdown("#### Approved actions")
            if not content.get("approved_actions"):
                st.caption("None")
            for a in content.get("approved_actions", []):
                st.markdown(
                    f"- **{a.get('description')}** "
                    f"(owner: {a.get('owner') or '—'}, deadline: {a.get('deadline') or '—'})"
                )
                show_evidence = st.checkbox(
                    "Show evidence",
                    key=f"summary_evidence_{s.get('id')}_{a.get('description', 'action')}",
                )
                if show_evidence and a.get("source_document_id"):
                    doc = db.get_document(a["source_document_id"])
                    if doc:
                        st.caption(
                            f"{doc.get('filename')} · section: {a.get('source_section') or '—'}"
                        )
                        st.code((doc.get("content") or "")[:500], language=None)
                    else:
                        st.caption("Source document unavailable.")

            st.markdown("#### Extracted items (reviewed)")
            for it in content.get("extracted_items", []):
                conf = "confirmed" if it.get("is_confirmed") else "interpreted"
                st.markdown(
                    f"- [{it.get('type')}/{conf}] {it.get('content')} "
                    f"— _{it.get('source_section')}_"
                )
                show_source_evidence = st.checkbox(
                    "Show source evidence",
                    key=f"summary_source_{s.get('id')}_{it.get('id', 'item')}",
                )
                if show_source_evidence:
                    excerpt = _evidence_excerpt(
                        it.get("source_document_id"),
                        int(it.get("source_offset_start") or 0),
                        int(it.get("source_offset_end") or 0),
                    )
                    st.code(excerpt or "(no excerpt)", language=None)

            st.markdown("#### Resolved conflicts")
            for c in content.get("resolved_conflicts", []):
                st.markdown(
                    f"- [{c.get('type')}] {c.get('description')} "
                    f"— _{c.get('resolution_note') or 'no note'}_"
                )

            st.markdown("#### Standards referenced")
            for m in content.get("kb_standards", []):
                st.markdown(f"- **{m.get('title')}** ({m.get('score', 0):.3f}): {m.get('snippet')}")

            st.download_button(
                "Download summary JSON",
                data=json.dumps(content, indent=2),
                file_name=f"reviewed_summary_{s.get('id')}.json",
                mime="application/json",
                key=f"dl_summary_{s.get('id')}",
            )


def render_results(session_id: int) -> None:
    st.subheader("2. Review results")
    render_session_metrics(session_id)

    docs = db.list_documents(session_id)
    if not docs:
        st.warning("No documents in this session.")
        return

    tab_labels = [d.get("filename") for d in docs] + [
        "Conflicts",
        "Standards",
        "Actions",
        "Review Summary",
    ]
    tabs = st.tabs(tab_labels)

    for i, doc in enumerate(docs):
        with tabs[i]:
            render_document_tab(doc)

    with tabs[len(docs)]:
        render_conflicts(session_id)
    with tabs[len(docs) + 1]:
        render_kb_matches(session_id)
    with tabs[len(docs) + 2]:
        render_action_items(session_id)
    with tabs[len(docs) + 3]:
        render_review_summary(session_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Document-to-Action",
        page_icon=":clipboard:",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()
    _init_state()
    render_auth_panel()
    if not st.session_state.get("authenticated", False):
        return
    render_hero()

    render_history_panel()

    if not llm_client.is_configured():
        st.warning(
            "No `GEMINI_API_KEY` found in the environment. "
            "The app will use heuristic extraction so you can still demo the workflow. "
            "Add a free key from [Google AI Studio](https://aistudio.google.com/apikey) "
            "to `.env` for full LLM quality."
        )

    render_upload_panel()

    if st.session_state.session_id and st.session_state.analysis_done:
        st.divider()
        render_results(st.session_state.session_id)
    elif st.session_state.session_id and not st.session_state.analysis_done:
        st.info("Session created but analysis did not finish. Re-run analysis.")


if __name__ == "__main__":
    main()

