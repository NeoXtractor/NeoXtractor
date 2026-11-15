# Agent instructions

Rules for AI coding agents working in this repository. Humans: see
CONTRIBUTING.md for the same contracts in contributor form.

## Scope and authority

- This is NeoXtractor, a Python tool for NeoX game archives. The v2
  architecture (packages / processors / viewers contracts) is the target
  state; do not reintroduce patterns it replaced.
- Authoritative sources, in order: the code itself, the contract module
  docstrings (`core/packages/protocol.py`, `core/processors/protocol.py`,
  `gui/viewers/base.py`), CONTRIBUTING.md, README files next to plugins and
  tools. Do not invent a parallel description of a format or contract; if
  docs and code disagree, report it instead of silently picking one.

## Workflow

1. Investigate first: read the relevant code and verify claims against real
   behavior (run probes, check samples) before proposing a change.
2. Propose, then wait. For anything beyond a trivial fix, present findings
   and a plan; implement only after the user approves. When the user is
   reviewing or reporting issues one by one, do not batch-fix unasked.
3. Keep changes scoped to the approved task. No drive-by refactors, no
   speculative features, no new dependencies without approval.

## Hard rules

- ASCII-only in all text files (source, comments, docstrings, UI strings,
  configs). Exception: data a format spec itself requires, with a source
  comment. LF line endings; never fight `.gitattributes`.
- Never modify user data: `settings.json` at the repo root is the user's
  live settings file (git-ignored). Tests and probes must use explicit
  temporary paths, never the default settings location.
- Do not add, remove, or bump dependencies in `pyproject.toml` without
  approval; any approved change requires refreshing `uv.lock`.
- Do not delete or rewrite game sample files. Read-only access to samples.

## Format-code rules

- No sample, no behavior change. Unknown layouts, versions, or flags degrade
  honestly (nameless entries, UNRECOGNIZED status, typed errors) -- never
  guess a layout, never serve undecoded bytes as decoded content.
- Port decoding mechanics with the reference control flow intact; copy key
  material and constants verbatim. Do not "simplify" branches without sample
  evidence. Do not transcribe format facts from memory into new documents;
  read them from the code or the reference implementation.
- Claims about compatibility or behavior must be demonstrated by running
  code, not asserted from reasoning.

## Architecture invariants

- Discovered modules are stateless: per-session data lives in the objects
  `load()` returns; module level holds immutable constants only.
- Layering is one-way: utils -> (nothing above); packages/processors ->
  utils; GUI -> packages + processors facade. The GUI never imports private
  implementations (`_mesh`, `_images`) and never branches on format names.
- Every package directory keeps an `__init__.py` serving as README.
- New modules under discovered packages must remain discoverable in frozen
  builds (check `main.spec` collection).

## Verification checklist (after each change)

- `python -m compileall neoxtractor` passes; the GUI still imports.
- `mypy` on touched files: no new errors. Inline suppressions only for
  proven upstream stub defects, with a comment.
- Behavior changes demonstrated against real samples where available.
- No non-ASCII introduced; `git status` shows only intended files.
- Throwaway probe/test scripts live outside the repository and are cleaned
  up; nothing temporary is committed.

## Documentation rules

- Docstrings and comments describe the target-state behavior and the
  constraints a maintainer must respect. No migration narratives
  ("previously X, now Y", "fixed because..."), no restating obvious code.
- Keep the contract docstrings authoritative; when behavior changes
  deliberately, update the contract text in the same change.