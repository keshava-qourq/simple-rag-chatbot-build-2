"""Secret hygiene checks (AC-065, AC-066).

These scan the repository itself rather than the running application: a
real key pasted into source or `.env.example`, or `.env` dropped from
`.gitignore`, is a defect no matter what the runtime behaviour looks like.
"""

import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_ROOT / "app"
ENV_EXAMPLE = BACKEND_ROOT / ".env.example"
GITIGNORE = BACKEND_ROOT / ".gitignore"

# Shapes real secrets commonly take. Deliberately broad: a false positive
# here just means tightening a pattern, a false negative means a leaked key.
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{15,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT-shaped
]

KEY_ENV_VAR_RE = re.compile(
    r"^\s*([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*)\s*=\s*(.+)$"
)


def _iter_app_source_files():
    yield from APP_DIR.rglob("*.py")


def test_no_secret_shaped_value_in_application_source() -> None:
    """AC-065: application source must not contain a real-looking key."""
    offenders = []
    for path in _iter_app_source_files():
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}: matches {pattern.pattern}")

    assert not offenders, f"Secret-shaped values found in source: {offenders}"


def test_env_example_has_no_secret_shaped_value() -> None:
    """AC-065: `.env.example` must only ship placeholders, never real values."""
    assert ENV_EXAMPLE.exists(), ".env.example must exist"
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    offenders = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            offenders.append(pattern.pattern)
    assert not offenders, f"Secret-shaped values found in .env.example: {offenders}"


def test_env_example_key_and_secret_variables_are_blank() -> None:
    """AC-065: any *_API_KEY/*_SECRET/*_TOKEN variable in the example file
    must be left blank -- a non-empty value there is not a placeholder."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    non_blank_sensitive = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        match = KEY_ENV_VAR_RE.match(line)
        if match:
            name, value = match.group(1), match.group(2).strip()
            if value:
                non_blank_sensitive.append(f"{name}={value}")

    assert not non_blank_sensitive, (
        f"Sensitive variables must be blank in .env.example: {non_blank_sensitive}"
    )


def test_gitignore_excludes_dotenv() -> None:
    """AC-066: `.env` must never be committed, so it must be git-ignored."""
    assert GITIGNORE.exists(), ".gitignore must exist"
    patterns = {line.strip() for line in GITIGNORE.read_text(encoding="utf-8").splitlines()}

    assert ".env" in patterns, ".gitignore must list `.env` so it is never committed"
