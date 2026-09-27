---
name: python-packaging-and-tooling
description: Set up and ship a modern Python project - pyproject.toml, src-layout, dependency groups and lockfiles (uv by default, poetry/pdm when forced), editable installs, ruff and mypy, entry points, and building/writing to a package index. Use when a user says "set up a new Python project", "requirements.txt vs pyproject", "uv/poetry/pdm", "ModuleNotFoundError: No module named", "import fails after pip install -e", "publish to PyPI", "expose a CLI". Triggers on "pyproject.toml", "setup.py", "lockfile", "editable install", "src layout", "entry point", "console_script".
compatibility: Python 3.11+ assumed. uv is the default resolver; poetry and pdm commands shown for existing projects. Verify flags with `uv --help` / `poetry --help` since they move between releases.
metadata:
  version: "1.0"
---

# Python Packaging & Tooling

Modern Python is one file. `pyproject.toml` holds metadata, dependencies, and
tool configuration; there is no `setup.py` and no `setup.cfg` unless you are
maintaining something old. Pick **uv** for a new project, `src/` layout always,
and make the import name match what you publish.

## Workflow

- [ ] 1. `uv init --package myproject` (creates pyproject, src layout, `.gitignore`)
- [ ] 2. Set `[project] name`, `requires-python`, `dependencies`
- [ ] 3. Put dev tooling in `[dependency-groups] dev = [...]` (PEP 735)
- [ ] 4. Configure `[tool.ruff]` and `[tool.mypy]` in the same file
- [ ] 5. `uv sync` then `uv run pytest` — verify the install before writing code
- [ ] 6. `uv build` then `uv publish` (or twine) once tests pass

## The default: uv, src layout, one file

```sh
uv init --package myproject          # pyproject.toml + src/myproject/ + .gitignore
cd myproject
uv add httpx                          # runtime dep -> [project.dependencies]
uv add --dev ruff mypy pytest         # dev dep -> [dependency-groups].dev
uv sync                               # creates .venv + uv.lock from pyproject
uv run pytest                         # run in the venv, no activation dance
```

`uv init --package` already produces the src layout and a working
`[build-system]`. Everything below is what to make correct in the file it
generates.

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]     # or "setuptools>=68", "poetry-core", "pdm-backend"
build-backend = "hatchling.build"

[project]
name = "myproject"                       # the DISTRIBUTION name (what you pip install)
version = "0.1.0"
description = "One line, shown on PyPI."
readme = "README.md"
requires-python = ">=3.11"               # hard floor; refuse installs below it
license = "MIT"                          # SPDX expression ("MIT", "Apache-2.0")
authors = [{ name = "Ada Lovelace" }]
dependencies = [
    "httpx>=0.27,<1.0",                 # range: >= lower, < upper cap
    "pydantic>=2.7",                     # no upper cap is fine for a library
]

[project.optional-dependencies]         # pip install myproject[redis]
redis = ["redis>=5"]

[project.scripts]                       # console_scripts entry points
myproject = "myproject.cli:main"        # the VALUE is import:func, not the package
myp-admin = "myproject.admin:entrypoint"

[project.entry-points."myproject.plugins"]
example = "myproject.plugins:ExamplePlugin"

[project.urls]
Repository = "https://github.com/you/myproject"

[dependency-groups]                     # PEP 735; dev-only, NOT published
dev = ["pytest>=8", "mypy>=1.10", "ruff>=0.5"]

# hatchling needs to know where the package is when the import name differs
# from the distribution name:
[tool.hatch.build.targets.wheel]
packages = ["src/myproject"]            # only needed for a renamed import name
```

Three dependency fields, three audiences — do not mix them up:

| Field | Published? | Installed by | Use for |
|---|---|---|---|
| `dependencies` | Yes | anyone, always | what the code imports at runtime |
| `optional-dependencies` | Yes (as extras) | `pip install x[extra]` | genuine optional features, a *user* choice |
| `dependency-groups` (PEP 735) | No | `pip install -e .[dev]`? No — `uv sync` / `pip` with the group flag only | dev tooling: pytest, mypy, ruff, coverage |

`dependency-groups` are never installed when someone installs your package, and
are **not** PEP 621 extras. Do not put `pytest` in `dependencies` — it makes
your package heavier for every consumer and is the single most common packaging
mistake. A `dev` extra in `[project.optional-dependencies]` is the older
equivalent and still works; groups are the current answer.

### Lockfiles

A lockfile pins exact resolved versions so every environment is reproducible.

| Tool | Lockfile | Install with groups | Publish backend |
|---|---|---|---|
| **uv** (default) | `uv.lock` | `uv sync` (default group `dev` is auto-installed) | any PEP 517 backend |
| poetry | `poetry.lock` | `poetry install` | `poetry-core` |
| pdm | `pdm.lock` | `pdm install` | `pdm-backend` |

Rules that matter:

- **Commit the lockfile** for applications and services. For libraries, do not —
  pin nothing and let the consumer resolve; a pinned transitive dep in a
  library is a constraint you impose on everyone.
- The lockfile is not portable across **operating systems or Python
  versions** by default; `uv` and `poetry` keep per-platform resolution so
  regenerate rather than copying between them. Never hand-edit one.
- In CI, install from the lockfile so tests run against the same versions as
  production: `uv sync --frozen` (uv) or `poetry install --sync`
  (poetry, 1.2+; `poetry install --no-root` in CI so the project itself is not
  installed into the runner). Verify the exact flags with your version.

## src-layout and the import-name trap

```
myproject/                 # repo root
  pyproject.toml
  src/
    myproject/             # the importable package
      __init__.py
      cli.py
```

Why `src/` matters: without it, `pytest` adds the repo root to `sys.path`, so
`import myproject` resolves to the **working directory copy**, not the
installed one. You get tests that pass against files you are not shipping, and
`ModuleNotFoundError` for anyone who installed the wheel (which has the package
under `site-packages/myproject`, but the CWD shadows it). `src/` makes the
local dir unimportable, so imports always come from the installed dist.

### The trap

The **distribution** name (what you `pip install`) and the **import** name
(the package under `src/`) are different namespaces and are allowed to differ —
but the build backend must be told, or it will ship an empty wheel.

```toml
[project]
name = "acme-tools"                       # distribution
[tool.hatch.build.targets.wheel]
packages = ["src/acme_tools"]             # import name; hatchling needs the hint
```

Mismatches and their symptoms:

- `name = "my-project"` but the folder is `my_project`, with no
  `packages = [...]` hint → `pip install -e .` succeeds and
  `import my_project` raises `ModuleNotFoundError: No module named 'my_project'`.
- Entry point `myproject = "myproject.cli:main"` but the *distribution* is
  `my-project` is fine — the script name and the distribution name are
  independent; only the **module path** in the value must be importable.
- A hyphen is fine in a distribution name (they are normalised to `_` in
  wheel filenames) but a hyphen is illegal in a Python import name.

Rule: **import name with underscores, distribution name with hyphens, and if
they differ, give the backend the mapping.**

## Editable installs

- **uv:** `uv sync` (or `uv pip install -e .`) installs the project editable by
  default in the venv. Use `uv sync --no-editable` to test the real wheel.
- **pip:** `pip install -e .` needs a PEP 660-capable backend. Modern
  setuptools, hatchling, poetry-core, and pdm-backend all support it. An old
  setuptools without PEP 660 falls back to a legacy `setup.py develop` shim and
  can silently not reflect source edits.
- Test the **installed** artefact before shipping:
  ```sh
  uv build                 # writes dist/*.whl and dist/*.tar.gz
  uv run --isolated --with ./dist/<wheel> python -c "import myproject"
  ```
  Editable passes but the wheel is missing a package or data file is the most
  common release bug.

## ruff and mypy

Put both in `pyproject.toml`; do not keep `ruff.toml`/`mypy.ini` split across
files.

```toml
[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF", "ASYNC", "S", "ANN"]
ignore = ["ANN101", "ANN102"]      # `self`/`cls` don't need annotations

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]               # asserts are fine in tests

[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.11"
strict = true                       # the single highest-value line here
warn_return_any = true
disallow_untyped_defs = true
plugins = ["pydantic.mypy"]        # makes BaseModel fields type-check

[[tool.mypy.overrides]]
module = ["some_untyped_lib.*"]
ignore_missing_imports = true       # per-module escape hatch, not global
```

- Run `ruff check .` and `ruff format .` — the formatter is opinionated, do not
  fight it. `ruff check --fix` applies safe fixes; `--unsafe-fixes` may change
  behaviour.
- `mypy --strict` on a codebase with no annotations is a mountain. Start with
  `warn_return_any` and `disallow_untyped_defs` and ratchet; do not set
  `ignore_errors` per file to make CI green, that defeats it.
- The `S` (bandit) rules catch real issues: `S` + `ASYNC` will flag
  `except:` , blocking calls inside `async def` (`ASYNC210` — blocking HTTP
  call in async function), and `time.sleep` in async code. These are the single
  highest-value Python lint set; see `python-async-patterns`.
- Wire all three into one pre-commit / CI step: `uv run ruff check . && uv run
  ruff format --check . && uv run mypy src && uv run pytest`.

## Publishing to an index

```sh
# 1. build
uv build                                   # or: python -m build
# 2. smoke-test the built artefact in a clean env (not editable!)
uv run --isolated --with dist/myproject-0.1.0-py3-none-any.whl python -c "import myproject; print(myproject.__version__)"
# 3. dry run against PyPI's staging, then publish
uv publish --check-url https://test.pypi.org/legacy/
twine upload --repository testpypi dist/*   # to TestPyPI first, always
uv publish dist/*                            # or: twine upload dist/*
```

- Build artifacts belong in `.gitignore` (`dist/`, `build/`, `*.egg-info`).
- TestPyPI first: it verifies upload + metadata without consuming a real
  version number. A version can only be published to a given index once, ever.
- Interactive TOTP-based uploads are deprecated. Use a PyPI API token stored as
  `__token__` / `pypi-…` in the environment as `UV_PUBLISH_TOKEN` (uv) or
  `TWINE_USERNAME`/`TWINE_PASSWORD` (twine). Never commit a token.
- Version numbers are immutable on an index. To ship again, bump the version.
- Include a `LICENSE` file and set `license`; do not rely only on metadata.

## Gotchas

- **`requirements.txt` is not the source of truth** for a new project. Keep it
  only as a generated `pip freeze` export for legacy consumers.
- **`sys.path` and CWD**: running `python` from the repo root of a non-src
  project imports the CWD copy first. This is the #1 "works on my machine"
  Python import bug; `src/` layout eliminates the class.
- **A `[build-system] requires` list is not your dependency list.** It is the
  build tooling; keep it minimal and unpinned-ish.
- **`requires-python` is enforced at install**; setting it too low then using
  3.12 syntax gives `SyntaxError` on the installer's 3.10, not a friendly
  error. Set it to your real floor and use Ruff/`target-version` to match.
- **Cython/Rust extensions** need a backend that compiles them; hatchling does
  not out of the box. If you ship compiled wheels, verify `build` produces one
  wheel per platform tag.
- **`poetry` and `pdm` are still fine** for existing projects; do not migrate a
  working build just to be modern. uv is the recommendation for greenfield and
  for consolidating a Makefile/CI into one tool.
- **`uv` does not install your project by default unless it is a package**;
  `uv init` (without `--package`) creates a script project with no installable
  package, and `uv run python -c "import x"` will fail. Use `--package`.
- Duplicate dependency in both `dependencies` and the dev group is resolved
  once, not twice — no conflict, but it hides a mistake about which one a
  runtime import actually needs.
