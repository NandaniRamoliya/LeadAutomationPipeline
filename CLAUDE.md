# CLAUDE.md

Before doing anything in this repository, read `docs/PROJECT_CONTEXT.md` (goal, decisions, current state, next steps)
and, for code work, `docs/DEVELOPER_GUIDE.md`.

- The owner prefers simple English and complete, finished work. Work on the `main` branch.
- Run `pytest` and `python -m pyflakes leadpipe tests scripts` before committing; keep all tests passing.
- Tests must not use the network or a real AI (`llm.provider: none`).
- Only email is ever sent automatically. WhatsApp/Instagram/LinkedIn/X/Facebook/calls stay one-click tasks.
- New settings go in `leadpipe/default_config.yaml` with a comment; database changes go through `db.MIGRATIONS`.
- After changing `docs/USER_GUIDE.md` or `docs/DEVELOPER_GUIDE.md`, rebuild the PDFs (`python scripts/build_docs.py`).
- Update `docs/PROJECT_CONTEXT.md` when something important is decided or finished.
