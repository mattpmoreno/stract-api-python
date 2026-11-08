"""Fetch the Stract OpenAPI spec and generate the Python SDK from it."""
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

SPEC_URL = "https://api.stract-api.com/spec/openapi.yaml"
ROOT = Path(__file__).parent
SPEC_PATH = ROOT / "input" / "openapi.yaml"
OUTPUT_DIR = ROOT / "output"
CONFIG_PATH = ROOT / "openapi-generator-config.yaml"


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
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    generate(fetch_spec())
