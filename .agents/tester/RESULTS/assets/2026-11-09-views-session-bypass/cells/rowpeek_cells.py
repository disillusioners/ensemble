#!/usr/bin/env python
"""Degradation-cells rowpeek — read-only PG introspection of the drill
view passcode row (root/rel from env; adapted from the 2026-10-18
brute-lane rowpeek.py). Needs POSTGRES_* (dev) + SYSTEM_ENCRYPTION_KEY
(the ONE drill fence key)."""
import os
import sys

from sqlalchemy import create_engine

from daemon.services.view_passcode_service import (
    ViewPasscodeCipher,
    ViewPasscodeService,
)

url = (
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
    f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
    f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)
svc = ViewPasscodeService(engine=create_engine(url), cipher=ViewPasscodeCipher())
root = os.environ["ROOT"]
rel = os.environ["VIEW_A"]
row = svc.get_row(root, rel)
if row is None:
    print(f"rowpeek {root}/{rel}: row=None")
else:
    print(
        f"rowpeek {root}/{rel}: failed_attempts={row.failed_attempts} "
        f"locked={bool(row.locked)} generation={row.generation}"
    )
