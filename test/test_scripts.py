"""Contract tests for the python scripts in airtable-automation-toolkit.

The repo root comes from the REPO environment variable and defaults to the
canonical local checkout:

    REPO=/path/to/airtable-automation-toolkit python -m pytest test/

What the suite checks:

- the source modules under src/ import cleanly from the repo path, and so
  do the example scripts under examples/
- every public function, class and method carries a docstring
- no hardcoded credentials: source and example text is scanned for token
  and key literal shapes, and secret named variables must not hold string
  literals, so secrets reach the code only through environment variables
  or explicit caller input
- examples read their credentials from the environment and load the
  environment file explicitly
- .env.example lists the expected variables with placeholder values only
- every module and file the README mentions exists in the repo
- modules do no work at import time (no client construction, no network)

The suite reads files and imports modules. It makes no network requests,
so it can run anywhere.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ.get("REPO", str(Path(__file__).resolve().parents[1]))).resolve()
SRC_DIR = REPO / "src"
EXAMPLES_DIR = REPO / "examples"
README_PATH = REPO / "README.md"
ENV_EXAMPLE_PATH = REPO / ".env.example"

SOURCE_MODULES = [
    "airtable_client",
    "batch_ops",
    "dead_letter_queue",
    "audit_logger",
    "staging_sync",
]

# Shapes of real provider credentials. A match is a finding unless the
# matched body is a single repeated character, which is how the repo
# spells placeholders.
CREDENTIAL_SHAPES = {
    "airtable personal access token": re.compile(r"pat[A-Za-z0-9]{14,}"),
    "airtable base id": re.compile(r"app[A-Za-z0-9]{14,}"),
    "airtable table id": re.compile(r"tbl[A-Za-z0-9]{14,}"),
    "sk-prefixed key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "github token": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "aws access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "bearer literal": re.compile(r"Bearer\s+[A-Za-z0-9_\-]{20,}"),
}
SHAPE_PREFIXES = ("pat", "app", "tbl", "sk-", "ghp_")

SECRET_NAME_RE = re.compile(
    r"(?i)(api_?key|api_?secret|token|secret|passwd|password|credential)"
)
PLACEHOLDER_VALUE_RE = re.compile(
    r"(?i)your|placeholder|change_?me|replace_?me|x{4,}|example|\$\{?env"
)

CLIENT_CLASS_NAMES = {
    "AirtableClient",
    "DeadLetterQueue",
    "AuditLogger",
    "StagingSync",
}


@pytest.fixture(scope="module", autouse=True)
def repo_on_path():
    """Put the repo root on the import path for the whole module."""
    inserted = str(REPO)
    sys.path.insert(0, inserted)
    importlib.invalidate_caches()
    yield
    if inserted in sys.path:
        sys.path.remove(inserted)


def source_files() -> list[Path]:
    if not SRC_DIR.is_dir():
        return []
    return sorted(path for path in SRC_DIR.iterdir() if path.suffix == ".py")


def example_files() -> list[Path]:
    if not EXAMPLES_DIR.is_dir():
        return []
    return sorted(path for path in EXAMPLES_DIR.iterdir() if path.suffix == ".py")


SOURCE_FILE_PARAMS = source_files() or [SRC_DIR / "MISSING-src-dir.py"]
EXAMPLE_FILE_PARAMS = example_files() or [EXAMPLES_DIR / "MISSING-examples-dir.py"]

SOURCE_PARAM_IDS = [f"src/{p.name}" for p in SOURCE_FILE_PARAMS]
EXAMPLE_PARAM_IDS = [f"examples/{p.name}" for p in EXAMPLE_FILE_PARAMS]


def read_text(path: Path) -> str:
    if not path.is_file():
        pytest.fail(f"file not found: {path}. Set REPO to the repo root.")
    return path.read_text(encoding="utf-8")


def import_source_module(name: str):
    return importlib.import_module("src." + name)


def import_package():
    return importlib.import_module("src")


def _is_uniform_run(fragment: str) -> bool:
    return len(fragment) > 0 and len(set(fragment)) == 1


def shape_findings(text: str) -> list[str]:
    """Report credential shaped strings that are not placeholders."""
    findings = []
    for label, pattern in CREDENTIAL_SHAPES.items():
        for match in pattern.finditer(text):
            token = match.group(0)
            body = token
            for prefix in SHAPE_PREFIXES:
                if body.startswith(prefix):
                    body = body[len(prefix):]
                    break
            body = re.sub(r"(?i)^bearer\s+", "", body)
            if _is_uniform_run(body):
                continue
            findings.append(f"{label}: {token}")
    return findings


def _looks_placeholder(value: str) -> bool:
    return bool(
        _is_uniform_run(value)
        or PLACEHOLDER_VALUE_RE.search(value)
        or len(value) < 8
    )


def secret_literal_findings(tree: ast.AST) -> list[str]:
    """Secret named variables or keyword arguments holding string literals."""
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
            names = [name for name in targets if SECRET_NAME_RE.search(name)]
            value = node.value
            if names and isinstance(value, ast.Constant) and isinstance(value.value, str):
                if not _looks_placeholder(value.value):
                    findings.append(
                        f"variable {', '.join(names)} holds the literal "
                        f"{value.value[:24]!r}"
                    )
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg and SECRET_NAME_RE.search(keyword.arg):
                    value = keyword.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        if not _looks_placeholder(value.value):
                            findings.append(
                                f"keyword {keyword.arg} holds the literal "
                                f"{value.value[:24]!r}"
                            )
    return findings


def module_level_action_findings(tree: ast.Module) -> list[str]:
    """Import time must stay side effect free: no clients, no requests."""
    findings = []
    for statement in tree.body:
        if isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in CLIENT_CLASS_NAMES:
                findings.append(f"module level {func.id}(...) construction")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "requests"
            ):
                findings.append(f"module level requests.{func.attr}(...) call")
    return findings


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def test_repo_layout_present():
    assert SRC_DIR.is_dir(), f"src directory not found at {SRC_DIR}"
    assert EXAMPLES_DIR.is_dir(), f"examples directory not found at {EXAMPLES_DIR}"
    assert README_PATH.is_file(), f"README not found at {README_PATH}"
    assert source_files(), "no python files found under src/"


# ---------------------------------------------------------------------------
# Importability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", SOURCE_MODULES)
def test_source_modules_import_from_the_repo_path(module_name: str):
    module = import_source_module(module_name)
    module_path = Path(module.__file__).resolve()
    assert module_path.parent == SRC_DIR, (
        f"{module_name} was imported from {module_path}, not from the repo"
    )
    members = [
        member
        for member in vars(module).values()
        if (inspect.isclass(member) or inspect.isfunction(member))
        and getattr(member, "__module__", "") == "src." + module_name
    ]
    assert members, f"src/{module_name}.py defines no class or function"


@pytest.mark.parametrize("path", EXAMPLE_FILE_PARAMS, ids=EXAMPLE_PARAM_IDS)
def test_example_scripts_import_cleanly(path: Path):
    module = importlib.import_module("examples." + path.stem)
    module_path = Path(module.__file__).resolve()
    assert module_path == path.resolve(), (
        f"{path.stem} was imported from {module_path}, not from the repo"
    )


def test_package_init_exports_the_documented_classes():
    package = import_package()
    for class_name in sorted(CLIENT_CLASS_NAMES):
        assert hasattr(package, class_name), (
            f"src/__init__.py does not export {class_name}"
        )


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", SOURCE_FILE_PARAMS, ids=SOURCE_PARAM_IDS)
def test_every_source_module_has_a_docstring(path: Path):
    tree = ast.parse(read_text(path))
    assert ast.get_docstring(tree), f"{path.name} has no module docstring"


@pytest.mark.parametrize("module_name", SOURCE_MODULES)
def test_public_functions_are_documented(module_name: str):
    module = import_source_module(module_name)
    undocumented = []
    for function_name, function in inspect.getmembers(module, inspect.isfunction):
        defined_here = function.__module__ == "src." + module_name
        if function_name.startswith("_") or not defined_here:
            continue
        if not inspect.getdoc(function):
            undocumented.append(function_name)
    for class_name, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ != "src." + module_name:
            continue
        for method_name, method in inspect.getmembers(cls, inspect.isfunction):
            if method_name.startswith("_"):
                continue
            if not inspect.getdoc(method):
                undocumented.append(f"{class_name}.{method_name}")
    assert not undocumented, (
        f"src/{module_name}.py leaves these public callables undocumented: "
        f"{undocumented}"
    )


@pytest.mark.parametrize("module_name", SOURCE_MODULES)
def test_public_classes_are_documented(module_name: str):
    module = import_source_module(module_name)
    undocumented = [
        class_name
        for class_name, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == "src." + module_name
        and not class_name.startswith("_")
        and not inspect.getdoc(cls)
    ]
    assert not undocumented, (
        f"src/{module_name}.py leaves these classes undocumented: {undocumented}"
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", SOURCE_FILE_PARAMS + EXAMPLE_FILE_PARAMS, ids=SOURCE_PARAM_IDS + EXAMPLE_PARAM_IDS)
def test_no_credential_shaped_literals(path: Path):
    findings = shape_findings(read_text(path))
    assert not findings, f"{path} contains credential shaped literals: {findings}"


@pytest.mark.parametrize("path", SOURCE_FILE_PARAMS + EXAMPLE_FILE_PARAMS, ids=SOURCE_PARAM_IDS + EXAMPLE_PARAM_IDS)
def test_no_secret_named_string_literals(path: Path):
    tree = ast.parse(read_text(path))
    findings = secret_literal_findings(tree)
    assert not findings, (
        f"{path} assigns secrets as literals; read them from the environment "
        f"instead: {findings}"
    )


def test_env_example_defines_the_expected_variables():
    text = read_text(ENV_EXAMPLE_PATH)
    for variable in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"):
        assert re.search(rf"^{variable}=", text, re.MULTILINE), (
            f".env.example does not define {variable}"
        )


@pytest.mark.parametrize(
    "variable", ["AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"]
)
def test_env_example_values_are_placeholders(variable: str):
    text = read_text(ENV_EXAMPLE_PATH)
    match = re.search(rf"^{variable}=(.*)$", text, re.MULTILINE)
    assert match, f".env.example does not define {variable}"
    value = match.group(1).strip()
    assert _looks_placeholder(value) and not shape_findings(value), (
        f".env.example value for {variable} looks like a real credential: {value!r}"
    )


# ---------------------------------------------------------------------------
# Examples read configuration from the environment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", EXAMPLE_FILE_PARAMS, ids=EXAMPLE_PARAM_IDS)
def test_examples_read_credentials_from_environment_variables(path: Path):
    text = read_text(path)
    assert re.search(r"os\.(getenv|environ)", text), (
        f"{path.name} never reads an environment variable"
    )
    for variable in ("AIRTABLE_API_KEY", "AIRTABLE_BASE_ID"):
        assert variable in text, (
            f"{path.name} does not reference {variable}"
        )


@pytest.mark.parametrize("path", EXAMPLE_FILE_PARAMS, ids=EXAMPLE_PARAM_IDS)
def test_examples_load_the_environment_file(path: Path):
    text = read_text(path)
    assert "load_dotenv" in text, (
        f"{path.name} does not call load_dotenv; environment setup is implicit"
    )


# ---------------------------------------------------------------------------
# Modules stay inert at import time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", SOURCE_FILE_PARAMS, ids=SOURCE_PARAM_IDS)
def test_no_module_level_client_construction_or_requests(path: Path):
    tree = ast.parse(read_text(path))
    findings = module_level_action_findings(tree)
    assert not findings, (
        f"{path.name} does work at import time: {findings}"
    )


# ---------------------------------------------------------------------------
# README references
# ---------------------------------------------------------------------------


def readme_text() -> str:
    return read_text(README_PATH)


def test_readme_imported_modules_exist():
    text = readme_text()
    modules = set(re.findall(r"from\s+src\.([a-z_][a-z0-9_]*)\s+import", text))
    assert modules, "README never shows a src module import"
    missing = sorted(name for name in modules if not (SRC_DIR / f"{name}.py").is_file())
    assert not missing, f"README imports src modules that do not exist: {missing}"


def test_readme_bullet_module_calls_exist():
    text = readme_text()
    calls = set()
    for line in text.splitlines():
        if not line.lstrip().startswith(("-", "*")):
            continue
        for match in re.finditer(
            r"(?<![A-Za-z0-9_.])([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\(", line
        ):
            calls.add(match.group(1))
    assert calls, "no README bullet references a module by name"
    missing = sorted(name for name in calls if not (SRC_DIR / f"{name}.py").is_file())
    assert not missing, f"README bullets call modules that do not exist: {missing}"


def test_readme_api_reference_classes_are_importable():
    text = readme_text()
    headers = re.findall(
        r"^#{2,4}\s+`?([A-Za-z_][A-Za-z0-9_]*)\(", text, re.MULTILINE
    )
    assert headers, "README API reference lists no classes"
    package = import_package()
    missing = sorted(name for name in headers if not hasattr(package, name))
    assert not missing, f"README documents classes that do not import: {missing}"


def test_readme_mentioned_python_paths_exist():
    text = readme_text()
    mentions = set(re.findall(r"(?<![\w/@.\-])([A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.py)\b", text))
    missing = sorted(name for name in mentions if not (REPO / name).is_file())
    assert not missing, f"README mentions python files absent from the repo: {missing}"


# ---------------------------------------------------------------------------
# Scanner self checks
# ---------------------------------------------------------------------------


def test_scanner_flags_an_obviously_fake_token():
    """Guard the guard: a credential shaped string must be a finding."""
    fake = "patQwErTyZx0987654321"
    assert shape_findings(fake), "scanner accepted a credential shaped literal"


def test_scanner_accepts_placeholder_runs():
    """Guard the guard: repeated character placeholders are not findings."""
    placeholders = "patXXXXXXXXXX appXXXXXXXXXXXXXX"
    assert not shape_findings(placeholders), (
        "scanner flagged the repo's placeholder spelling"
    )
