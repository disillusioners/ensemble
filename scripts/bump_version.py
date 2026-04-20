#!/usr/bin/env python3
"""Bump version across all project files atomically."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Version bump types
BUMP_TYPES = {
    "patch": (0, 1),  # 1.0.0 -> 1.0.1
    "minor": (1, 1),  # 1.0.0 -> 1.1.0
    "major": (2, 1),  # 0.1.0 -> 1.0.0
}


def parse_version(v: str) -> tuple[int, ...]:
    """Parse version string to tuple."""
    return tuple(int(x) for x in v.lstrip("v").split("-")[0].split("."))


def format_version(parts: tuple[int, ...], prerelease: str = "") -> str:
    """Format version tuple to string."""
    base = ".".join(str(p) for p in parts)
    return f"{base}-{prerelease}" if prerelease else base


def bump_version(version: str, bump_type: str = "patch") -> str:
    """Bump version according to type."""
    if bump_type == "prerelease":
        parts = parse_version(version)
        return f"{parts[0]}.{parts[1]}.{parts[2]}-beta.1"

    idx, inc = BUMP_TYPES[bump_type]
    parts = list(parse_version(version))
    parts[idx] += inc
    # Zero out lower parts when bumping major/minor
    for i in range(idx + 1, len(parts)):
        parts[i] = 0
    return format_version(tuple(parts))


def replace_in_file(file_path: Path, old: str, new: str) -> bool:
    """Replace version in file. Returns True if changed."""
    if not file_path.exists():
        return False
    content = file_path.read_text()
    if old not in content:
        return False
    file_path.write_text(content.replace(old, new, 1))
    return True


def get_current_version() -> str:
    """Read version from pyproject.toml."""
    pyproject = ROOT / "pyproject.toml"
    content = pyproject.read_text()
    match = re.search(r'^version\s*=\s*["\'](\S+)["\']', content, re.MULTILINE)
    if match:
        return match.group(1)
    raise ValueError("Could not find version in pyproject.toml")


def main():
    if len(sys.argv) < 2:
        bump_type = "patch"
    else:
        bump_type = sys.argv[1].lower()
        if bump_type not in BUMP_TYPES and bump_type not in ("prerelease", "beta"):
            print(f"Unknown bump type: {bump_type}")
            print(f"Valid types: patch, minor, major, prerelease")
            sys.exit(1)

    current = get_current_version()
    new_version = bump_version(current, bump_type)

    print(f"Bumping {current} -> {new_version}")

    # Files to update (pattern, new content generator)
    files_updated = []

    # pyproject.toml
    pyproject = ROOT / "pyproject.toml"
    if replace_in_file(pyproject, f'version = "{current}"', f'version = "{new_version}"'):
        files_updated.append("pyproject.toml")

    # daemon/__init__.py
    daemon_init = ROOT / "daemon" / "__init__.py"
    if replace_in_file(daemon_init, f'__version__ = "{current}"', f'__version__ = "{new_version}"'):
        files_updated.append("daemon/__init__.py")

    # tests/test_api.py
    test_api = ROOT / "tests" / "test_api.py"
    if replace_in_file(test_api, f'version"] == "{current}"', f'version"] == "{new_version}"'):
        files_updated.append("tests/test_api.py")

    # uv.lock (only our package entry)
    uv_lock = ROOT / "uv.lock"
    if uv_lock.exists():
        content = uv_lock.read_text()
        # Find and replace only the "ensemble" package entry
        pattern = r'(name = "ensemble"\nversion = )"[0-9.]+"\n'
        replacement = rf'\g<1>"{new_version}"\n'
        new_content = re.sub(pattern, replacement, content)
        if new_content != content:
            uv_lock.write_text(new_content)
            files_updated.append("uv.lock")

    print(f"\nUpdated {len(files_updated)} files:")
    for f in files_updated:
        print(f"  - {f}")

    # Run uv sync to regenerate lockfile
    print("\nRunning uv sync...")
    subprocess.run(["uv", "sync"], check=True)

    # Git commit
    print("\nCreating commit...")
    subprocess.run(["git", "add", "-A"], check=True, cwd=ROOT)
    result = subprocess.run(
        ["git", "commit", "-m", f"! New version {new_version}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Commit created: {result.stdout.strip()}")
    else:
        print(f"Commit failed: {result.stderr}")
        sys.exit(1)

    print(f"\n✅ Version bumped to {new_version}")


if __name__ == "__main__":
    main()
