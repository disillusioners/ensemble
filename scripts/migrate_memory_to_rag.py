#!/usr/bin/env python3
"""Migrate file-based memories to RAG knowledge base.

Usage:
    python scripts/migrate_memory_to_rag.py --all
    python scripts/migrate_memory_to_rag.py --agent coder
    python scripts/migrate_memory_to_rag.py --dry-run --all
    python scripts/migrate_memory_to_rag.py --project-dir /path/to/project --all
"""

import argparse
import os
import re
import sys
from pathlib import Path
from datetime import datetime

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
        "workspace": os.environ.get("LIGHTRAG_WORKSPACE", "default"),
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


def insert_to_rag(config: dict, text: str, description: str, dry_run: bool = False) -> bool:
    """Insert text into RAG knowledge base."""
    if dry_run:
        return True

    url = f"{config['host']}/api/documents/upload_texts"
    headers = {"Content-Type": "application/json"}
    if config["api_key"]:
        headers["Authorization"] = f"Bearer {config['api_key']}"
    if config["workspace"]:
        headers["X-Workspace"] = config["workspace"]

    payload = {
        "texts": [{"text": text, "description": description}]
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
    parser.add_argument("--project-dir", type=str, default=".", help="Project directory")
    parser.add_argument("--rag-host", type=str, help="Override LIGHTRAG_HOST")
    args = parser.parse_args()

    if not args.all and not args.agent:
        parser.error("Must specify --all or --agent <name>")

    project_dir = Path(args.project_dir).resolve()
    config = get_rag_config(args)

    print(f"Project: {project_dir}")
    print(f"RAG Host: {config['host']}")
    print(f"Workspace: {config['workspace']}")
    if args.dry_run:
        print("DRY RUN — no changes will be made")
    print()

    files = find_memory_files(project_dir, args.agent)

    if not files:
        print("No memory files found.")
        return

    print(f"Found {len(files)} memory file(s)\n")

    imported = 0
    skipped = 0
    errors = 0

    for agent_name, filepath in files:
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

        description = f"[Migration from {agent_name}] {filepath.stem}"
        tagged_text = f"[Source: {agent_name}] [Date: {date}] [Migration]\n\n{content}"

        print(f"  {'[DRY RUN] ' if args.dry_run else ''}{filepath.name} ({len(content)} chars)")

        success = insert_to_rag(config, tagged_text, description, args.dry_run)
        if success:
            imported += 1
        else:
            errors += 1

    print(f"\n{'='*40}")
    print(f"Imported: {imported}")
    print(f"Skipped:  {skipped}")
    print(f"Errors:   {errors}")
    print(f"Total:    {len(files)}")

    if not args.dry_run and imported > 0:
        print(f"\n Original files NOT deleted. Remove manually after verification.")


if __name__ == "__main__":
    main()
