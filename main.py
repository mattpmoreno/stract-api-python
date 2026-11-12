"""Fetch the Stract OpenAPI spec and generate the Python SDK from it."""
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

# Where to fetch the spec from. Set STRACT_API_BASE_URL (e.g. staging) in .env to
# generate from a not-yet-deployed spec; unset, the production spec is used.
# Every deployment's spec is expected to list production in its `servers:` block.
SPEC_BASE_URL = os.environ.get("STRACT_API_BASE_URL") or "https://api.stract-api.com"
SPEC_URL = f"{SPEC_BASE_URL.rstrip('/')}/spec/openapi.yaml"
SPEC_PATH = ROOT / "input" / "openapi.yaml"
OUTPUT_DIR = ROOT / "output"
CONFIG_PATH = ROOT / "openapi-generator-config.yaml"
TEMPLATE_DIR = ROOT / "templates"  # overrides for the generator's built-in templates


def fetch_spec() -> Path:
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Fetching spec from {SPEC_URL}")
    with urllib.request.urlopen(SPEC_URL, timeout=30) as resp:
        SPEC_PATH.write_bytes(resp.read())
    print(f"Saved spec to {SPEC_PATH}")
    return SPEC_PATH


def generate(spec: Path) -> None:
    # Prefer the venv's installed CLI, then fall back to PATH.
    venv_scripts = Path(sys.executable).parent
    cli = shutil.which("openapi-generator-cli", path=str(venv_scripts)) or shutil.which("openapi-generator-cli")
    if cli is None:
        sys.exit("openapi-generator-cli not found. Run: uv add --dev openapi-generator-cli (Java required)")

    cmd = [
        cli, "generate",
        "-i", str(spec),
        "-g", "python",
        "-o", str(OUTPUT_DIR),
        "-c", str(CONFIG_PATH),
        "-t", str(TEMPLATE_DIR),
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    # Ship the MIT license with the package (referenced by license-files in pyproject).
    shutil.copyfile(ROOT / "LICENSE", OUTPUT_DIR / "LICENSE")


if __name__ == "__main__":
    generate(fetch_spec())
