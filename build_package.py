"""
Build and verify havi-mcd-foundry-production.zip for Azure AI Foundry Hosted Agent.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent
OUTPUT_ZIP = WORKSPACE_ROOT / "havi-mcd-foundry-production.zip"

INCLUDE_ROOT_FILES = [
    "main.py",
    "hosted_server.py",
    "config.py",
    "runtime_paths.py",
    "requirements.txt",
    "README.md",
    ".env.example",
    "azure.yaml",
    "pytest.ini",
    "deploy_foundry_agent.py",
    "validate_production.py",
]

INCLUDE_DIRS = [
    "agents",
    "orchestration",
    "tools",
    "config",
    "persistence",
    "monitoring",
    "schemas",
    "validation",
    "adapters",
    "document_processing",
    "tests",
    "test_data",
    ".azure",
]

EXCLUDE_PATTERNS = [
    "__pycache__",
    ".pyc",
    ".pyo",
    ".git",
    ".pytest_cache",
    ".env",
    "output",
    "temp",
    "uploads",
    ".zip",
    "app_logs",
    "latest_logs",
]


def should_exclude(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/")
    parts = normalized.split("/")

    for part in parts:
        if part in ("__pycache__", ".git", ".pytest_cache", "output", "temp", "uploads", "archive"):
            return True
        if part == ".env":
            return True
    
    if normalized.endswith((".pyc", ".pyo", ".zip")):
        return True
    
    return False


def build_zip() -> None:
    print(f"Building deployment ZIP: {OUTPUT_ZIP.name}")
    if OUTPUT_ZIP.exists():
        OUTPUT_ZIP.unlink()

    total_files = 0

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Add root files
        for filename in INCLUDE_ROOT_FILES:
            file_path = WORKSPACE_ROOT / filename
            if file_path.exists():
                zf.write(file_path, arcname=filename)
                total_files += 1
                print(f"  [+] {filename}")
            else:
                print(f"  [!] Missing root file: {filename}")

        # 2. Add directories
        for dirname in INCLUDE_DIRS:
            dir_path = WORKSPACE_ROOT / dirname
            if not dir_path.exists():
                print(f"  [!] Directory not found: {dirname}")
                continue

            for root, _, files in os.walk(dir_path):
                for f in files:
                    full_p = Path(root) / f
                    rel_p = full_p.relative_to(WORKSPACE_ROOT)
                    rel_str = str(rel_p)

                    if should_exclude(rel_str):
                        continue

                    arcname = rel_str.replace("\\", "/")
                    zf.write(full_p, arcname=arcname)
                    total_files += 1

    print(f"\nZIP package created: {OUTPUT_ZIP} ({OUTPUT_ZIP.stat().st_size:,} bytes, {total_files} files)")

    # 3. Verify ZIP integrity
    print("\nVerifying ZIP integrity...")
    with zipfile.ZipFile(OUTPUT_ZIP, "r") as zf:
        test_res = zf.testzip()
        if test_res is not None:
            raise RuntimeError(f"Corrupt file in ZIP: {test_res}")

        file_list = zf.namelist()
        print(f"  - Verified {len(file_list)} files without CRC errors.")

        # Assert key files at root
        for expected in ["main.py", "requirements.txt", "azure.yaml", "README.md", ".env.example"]:
            assert expected in file_list, f"Missing root file in archive: {expected}"
            print(f"  - Confirmed root member: {expected}")

        # Assert no illegal files
        for f in file_list:
            assert not f.startswith(".git/"), f"Illegal git file in zip: {f}"
            assert "/__pycache__/" not in f, f"Illegal pycache in zip: {f}"
            assert not f.endswith(".pyc"), f"Illegal pyc in zip: {f}"
            assert f != ".env", f"Illegal secret .env in zip: {f}"

    print("ZIP verification passed successfully!\n")


if __name__ == "__main__":
    build_zip()
