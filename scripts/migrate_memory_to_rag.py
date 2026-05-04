#!/usr/bin/env python3
"""Migrate file-based memories to RAG knowledge base.

Usage:
    python scripts/migrate_memory_to_rag.py --all
    python scripts/migrate_memory_to_rag.py --agent coder
    python scripts/migrate_memory_to_rag.py --dry-run --all
    python scripts/migrate_memory_to_rag.py --force --all
    python scripts/migrate_memory_to_rag.py --project-dir /path/to/project --all

IMPORTANT: This script should only be run ONCE per project. Running multiple times
may create duplicates. Use --dry-run first to preview what will be migrated.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# State file for tracking migrated files
STATE_FILENAME = ".rag_migration_state.json"


def get_state_file(project_dir: Path) -> Path:
    """Get path to state file."""
    return project_dir / STATE_FILENAME


def load_state(project_dir: Path) -> dict:
    """Load migration state from file."""
    state_file = get_state_file(project_dir)
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"WARNING: Could not read state file: {e}")
    return {"migrated_files": {}}


def save_state(project_dir: Path, state: dict) -> None:
    """Save migration state to file."""
    state_file = get_state_file(project_dir)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def is_migrated(state: dict, filepath: Path) -> bool:
    """Check if file is already in migrated state."""
    return str(filepath) in state.get("migrated_files", {})


def mark_migrated(state: dict, filepath: Path) -> None:
    """Mark file as migrated in state."""
    if "migrated_files" not in state:
        state["migrated_files"] = {}
    state["migrated_files"][str(filepath)] = datetime.now().isoformat()


def compute_content_hash(content: str, agent_name: str, filename: str) -> str:
    """Compute SHA-256 hash of content for deduplication."""
    data = f"{agent_name}:{filename}:{content}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def check_hash_exists_in_rag(config: dict, content_hash: str) -> bool:
    """Check if an entry with this content hash already exists in RAG."""
    url = f"{config['host']}/api/documents/list"
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    if config["workspace"]:
        headers["X-Workspace"] = config["workspace"]

    try:
        resp = httpx.get(url, headers=headers, timeout=config["timeout"])
        if resp.status_code == 200:
            data = resp.json()
            # Search for the hash in document descriptions/metadata
            for doc in data.get("documents", []):
                desc = doc.get("description", "") or ""
                if content_hash in desc:
                    return True
        return False
    except Exception:
        return False


try:
    import httpx
except ImportError:
    try:
        import requests as httpx
    except ImportError:
        print("ERROR: Install httpx or requests: pip install httpx")
        sys.exit(1)


def get_rag_config(args):
    """Get RAG configuration from args or environment."""
    host = args.rag_host or os.environ.get("LIGHTRAG_HOST", "")
    if not host:
        print("ERROR: LIGHTRAG_HOST not set. Use --rag-host or set LIGHTRAG_HOST env var.")
        sys.exit(1)

    return {
        "host": host.rstrip("/"),
        "api_key": os.environ.get("LIGHTRAG_API_KEY", ""),
        "workspace": os.environ.get("LIGHTRAG_WORKSPACE", ""),
        "timeout": int(os.environ.get("LIGHTRAG_TIMEOUT", "60")),
    }


def find_memory_files(project_dir: Path, agent_name: str | None = None):
    """Find all memory files for specified agent(s)."""
    agents_dir = project_dir / ".agents"
    if not agents_dir.exists():
        print(f"ERROR: .agents/ directory not found at {project_dir}")
        return []

    files = []
    if agent_name:
        # Specific agent
        memories_dir = agents_dir / agent_name / "memories"
        if memories_dir.exists():
            for f in sorted(memories_dir.glob("*.md")):
                files.append((agent_name, f))
    else:
        # All agents
        for agent_dir in sorted(agents_dir.iterdir()):
            if agent_dir.is_dir() and agent_dir.name != "shared":
                memories_dir = agent_dir / "memories"
                if memories_dir.exists():
                    for f in sorted(memories_dir.glob("*.md")):
                        files.append((agent_dir.name, f))

    return files


def extract_date_from_filename(filename: str) -> str:
    """Extract date from memory filename."""
    # Format: 20260405_1012-descriptive-title.md or 2026-04-05-title.md
    match = re.match(r"(\d{8})_?(\d{4})?-", filename)
    if match:
        date_str = match.group(1)
        time_str = match.group(2) or "0000"
        try:
            dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M")
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return date_str
    return "unknown"


def insert_to_rag(
    config: dict, text: str, description: str, content_hash: str, dry_run: bool = False
) -> bool:
    """Insert text into RAG knowledge base."""
    if dry_run:
        return True

    url = f"{config['host']}/api/documents/upload_texts"
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    if config["workspace"]:
        headers["X-Workspace"] = config["workspace"]

    # Include content hash in description for deduplication detection
    full_description = f"{description} [hash:{content_hash}]"
    payload = {
        "texts": [{"text": text, "description": full_description}]
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=config["timeout"])
        if resp.status_code == 200:
            return True
        else:
            print(f"\n  ERROR: HTTP {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"\n  ERROR: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Migrate memories to RAG knowledge base")
    parser.add_argument("--all", action="store_true", help="Process all agents")
    parser.add_argument("--agent", type=str, help="Process specific agent")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported")
    parser.add_argument("--force", action="store_true", help="Ignore state file and re-migrate all files")
    parser.add_argument("--project-dir", type=str, default=".", help="Project directory")
    parser.add_argument("--rag-host", type=str, help="Override LIGHTRAG_HOST")
    args = parser.parse_args()

    if not args.all and not args.agent:
        parser.error("Must specify --all or --agent <name>")

    # Prominent warning - script should only be run once
    print("\n" + "!" * 60)
    print("  WARNING: This script should only be run ONCE per project.")
    print("  Running multiple times may create duplicate entries.")
    print("  Use --dry-run first to preview what will be migrated.")
    print("!" * 60 + "\n")

    project_dir = Path(args.project_dir).resolve()
    config = get_rag_config(args)
    state = load_state(project_dir)

    print(f"Project: {project_dir}")
    print(f"RAG Host: {config['host']}")
    print(f"Workspace: {config['workspace']}")
    if args.dry_run:
        print("DRY RUN — no changes will be made")
    if args.force:
        print("FORCE MODE — ignoring state file, will re-migrate all files")
    print()

    files = find_memory_files(project_dir, args.agent)

    if not files:
        print("No memory files found.")
        return

    print(f"Found {len(files)} memory file(s)\n")

    imported = 0
    skipped = 0
    already_migrated = 0
    duplicates = 0
    errors = 0

    for agent_name, filepath in files:
        # Check if already migrated (skip unless --force is used)
        if not args.force and is_migrated(state, filepath):
            print(f"  [SKIP] {filepath.name}: already migrated")
            already_migrated += 1
            continue

        date = extract_date_from_filename(filepath.name)

        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  SKIP {filepath.name}: {e}")
            skipped += 1
            continue

        # Skip empty or very short files
        if len(content.strip()) < 10:
            print(f"  SKIP {filepath.name}: too short")
            skipped += 1
            continue

        # Compute content hash for deduplication
        content_hash = compute_content_hash(content, agent_name, filepath.name)

        # Check if this exact content is already in RAG (hash-based dedup)
        if not args.force and check_hash_exists_in_rag(config, content_hash):
            print(f"  [SKIP] {filepath.name}: duplicate content (hash:{content_hash})")
            duplicates += 1
            continue

        description = f"[Migration from {agent_name}] {filepath.stem}"
        tagged_text = f"[Source: {agent_name}] [Date: {date}] [Migration]\n\n{content}"

        print(f"  {'[DRY RUN] ' if args.dry_run else ''}{filepath.name} ({len(content)} chars) [hash:{content_hash}]")

        success = insert_to_rag(config, tagged_text, description, content_hash, args.dry_run)
        if success:
            imported += 1
            # Mark as migrated only if not dry-run
            if not args.dry_run:
                mark_migrated(state, filepath)
                save_state(project_dir, state)
        else:
            errors += 1

    print(f"\n{'='*40}")
    print(f"Imported:        {imported}")
    print(f"Skipped:         {skipped}")
    print(f"Already Migrated: {already_migrated}")
    print(f"Duplicates:      {duplicates}")
    print(f"Errors:          {errors}")
    print(f"Total:           {len(files)}")

    if not args.dry_run and imported > 0:
        print(f"\n State saved to {STATE_FILENAME}")
        print(f" Original files NOT deleted. Remove manually after verification.")


if __name__ == "__main__":
    main()
