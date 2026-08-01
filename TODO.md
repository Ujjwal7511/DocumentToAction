# TODO — Document-to-Action Project Assistant (MongoDB Migration)

## Migration status: COMPLETE

The data layer has been fully migrated from SQLite/SQLAlchemy to MongoDB/PyMongo.
Remaining work in this pass is cleanup + documentation + verification.

- [x] Review all files: `db.py` (PyMongo migration), `app.py`, `auth.py`, `extract.py`, `conflicts.py`, `kb.py`, `ingest.py`, `llm_client.py`
- [x] Delete old SQLite file `data/document_to_action.db`
- [x] Fix `db.py` — add `updated_at` to `_out_action()` normalizer
- [x] Fix `auth.py` — remove dead `_users_store_path()` / `DTA_USERS_PATH` / `os`/`Path` imports
- [x] Update `tests/test_auth.py` — remove `DTA_USERS_PATH` monkeypatch, use MongoDB-backed auth
- [x] Update `README.md` — replace all SQLite/SQLAlchemy references with MongoDB/PyMongo
- [x] Rewrite `.env.example` — MongoDB-only config with `MONGODB_URI` + `MONGODB_DB_NAME` documented
- [x] Install `pymongo` + `pytest` (tests run against the real MongoDB Atlas URI from `.env`)
- [x] Remove diagnostic files (`smoke_test.py`, `diag_mongo.py`, `diag_network.py`)
- [x] Remove stale artifacts (`smoke_test_output.txt`, `diag_mongo_output.txt`, temp grep/check files)
- [x] Update `SPEC.md` — remove SQLAlchemy/SQLite from tech stack
- [x] Update `AGENT_USAGE.md` — remove SQLAlchemy `before_insert` / SQLite-default test references
- [x] Add MongoDB Atlas setup instructions to `README.md`
- [x] Final verification: zero `sqlalchemy`/`sqlite` references, `pytest` green, live Mongo connectivity check

