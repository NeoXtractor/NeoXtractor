# Contributing to NeoXtractor

Thanks for your interest! Read this before browsing or changing the code:
it covers the project's shape, the rules that are not visible from any
single file, and the acceptance standards a contribution must meet.
Per-module contracts live in the code itself -- each layer's `protocol.py`
/ `base.py` docstring is the authoritative description; this document does
not repeat them.

## Repository layout

```
main.py                     thin launcher -> neoxtractor.__main__
neoxtractor/
    core/
        packages/           container formats
            base.py         neutral vocabulary (Archive, EntryInfo, Claim, ...)
            protocol.py     container-module contract validation
            registry.py     discovery + unit-assembly driver
        processors/         format-processor facade (probe/read/convert)
        resources/          built-in processors; private modules
        utils/              neutral helpers (filelike, wrappers, result, ...)
    gui/
        viewers/            viewer contract + registry + all viewers
        models/             archive source, list model, entry filter
        windows/ widgets/ renderers/ ...
plugins/                    user plugin drop folder
data/                       runtime data: shaders, fonts, themes, syntax
                            definitions, codebooks (see coding rule 4)
tools/                      build helpers (shaders, build info)
```

Every package directory has an `__init__.py` whose one-line docstring states
the directory's responsibility. Keep it that way.

Runtime data lives in the top-level `data/` tree only -- the `neoxtractor/`
package holds code and nothing else.

## Development setup

- Python **3.12+**.
- Install in editable mode: `pip install -e .` -- or just run from a checkout
  with the dependencies installed: `python main.py` (also `python -m
  neoxtractor`). Global options: `--version`, `--log-level`.
- CI uses [uv](https://github.com/astral-sh/uv) (`uv sync --locked`) and
  PyInstaller (`main.spec`). You do not need uv for local development, but
  **any change to `pyproject.toml` dependencies or `requires-python` must be
  accompanied by a refreshed `uv.lock`** (`uv lock`), or CI fails.
- Discovery-based registries (packages, processors, viewers) enumerate
  subpackages at runtime; `main.spec` collects them explicitly for frozen
  builds. If you add a new top-level module under a discovered package,
  check the spec still collects it.

## Architecture in one paragraph

Three discovered registries, one contract each: `core/packages/` (container
formats), `core/processors/` + `core/resources/` (content decoding), and
`gui/viewers/` (preview). Modules are found by directory/naming convention
at runtime, validated against their layer's contract, and must be stateless.
The GUI reaches containers through `packages` and content through the
`processors` facade -- never through private implementations and never by
format name. Before writing a module for any layer, read its contract
docstring (`packages/protocol.py`, `processors/protocol.py`,
`gui/viewers/base.py`); it states the required members and the review
invariants.

## Adding things

- **A container format**: new directory under `core/packages/`, contract in
  `packages/protocol.py`.
- **A format processor**: prefer a plugin first -- copy
  `plugins/format_processors/_example_plugin.py` and follow the README.txt
  there. Built-in readers live in `core/resources/` behind the same contract.
- **A viewer**: new `*_viewer` module (or package) under `gui/viewers/`,
  contract in `gui/viewers/base.py`.

## Coding rules

1. **ASCII-only text files.** Source, comments, docstrings, UI strings, JSON,
   TOML, QSS -- English/ASCII. The only exception is data a format spec itself
   requires (e.g. PMX display-slot names), with a nearby comment citing the
   source. Binary assets are exempt.
2. **LF line endings** everywhere (enforced by `.gitattributes`; do not
   override it).
3. **Type checking.** `mypy` is configured in `pyproject.toml`. Keep the files
   you touch clean. The only sanctioned blanket exception is
   `ignore_missing_imports` for stub-less third-party libraries (already
   listed there). An inline suppression needs a comment proving an upstream
   stub defect, not a workaround for our own typing.
4. **Runtime data files live in the top-level `data/` directory**, never
   inside the `neoxtractor/` package. The resource root is resolved at
   runtime by `neoxtractor.core.utils.app.get_application_path()`, which
   probes the layouts a PyInstaller bundle, an installed wheel, and a dev
   checkout each produce; the packaging config (`pyproject.toml`
   force-include, `main.spec` datas) ships `data/` and `configs/` as a unit.
   Load such a file as `os.path.join(get_application_path(), "data",
   <subdir>, <name>)` -- a `__file__`-relative path breaks in frozen builds,
   and a package-internal blob needs its own special-casing in every
   packaging config. Pick the subdirectory by kind (`fonts`, `shaders`,
   `themes`, `codebooks`, ...); add a new one when a new data class appears.
   License texts that must travel with the code they cover are the one
   exception: keep them beside their module (e.g. `_wem/LICENSE.ww2ogg.md`).

## Acceptance standards

A change is acceptable when all of the following hold. There is no committed
test suite yet; verify with throwaway scripts kept outside the repository.

- `python -m compileall neoxtractor` passes.
- The GUI imports and starts (`python main.py`).
- `mypy` on the files you changed reports no new errors.
- Behavior changes are demonstrated against real samples (before/after).
- No non-ASCII introduced into text files; LF preserved.
- `git status` shows only intended files; no temporary scripts, samples, or
  user settings staged.
- Contract docstrings updated in the same change when behavior changes
  deliberately.

## Commits and licensing

- Commit messages follow the conventional style (`refactor:`, `feat:`,
  `fix:`, `docs:`, `chore:`) with a body explaining what and why.
- By contributing you agree your contribution is licensed under
  **AGPL-3.0**, the project's license (see `LICENSE`).