# AGENTS.md — Guidance for AI coding agents

This short guide helps AI coding agents (and humans acting as agents) get productive in this repository quickly.

Core quick-start
- **Install dev deps:** `poetry install --with dev` (or `pip install -e ".[dev]"`).
- **Run tests:** `poetry run pytest` (or `poetry run pytest <test_file>::<test_func>`).
- **Build (PyInstaller):** `poetry run pyinstaller simkl-mps.spec` and verify with `python test_build.py [windows|macos|linux]`.

Important locations
- **Source:** `simkl_mps/` — main application modules (monitoring, scrobbling, players).
- **Players:** `simkl_mps/players/` — per-player integrations; follow the `get_position_duration()` interface.
- **Simkl API:** `simkl_mps/simkl_api.py` (the client) and the `simkl-api` skill (endpoint reference).
- **Docs & guides:** `docs/` (platform guides, configuration, troubleshooting).

Agent-specific files
- `.github/copilot-instructions.md` — longer, human-focused instructions (also links to this file).
- `.github/agents/simkl-expert.agent.md` — specialist agent for Simkl API tasks.
- `.github/skills/simkl-api/SKILL.md` — reusable skill with authentication and scrobble workflows.

Agent best practices
- **Link, don't duplicate:** Refer to `docs/` or existing files for details; include only the minimal facts an agent needs.
- **Use skills:** The `simkl-api` skill contains the canonical API checklist — invoke it for API work.
- **Run tests early:** Use `poetry run pytest` before proposing code changes that modify behavior.
