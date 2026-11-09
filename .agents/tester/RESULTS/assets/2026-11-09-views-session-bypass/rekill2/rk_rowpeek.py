"""rk_rowpeek.py — RE-KILL 2 in-DB strike ledger (both drill rows).

Read-only. Uses the drill Fernet key from the fence (env SYSTEM_ENCRYPTION_KEY).
"""
import os

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
for root, rel in (
    (os.environ["ROOT"], os.environ["VIEW_R"]),
    ("board", "index.html"),
):
    row = svc.get_row(root, rel)
    if row is None:
        print(f"{root}/{rel}: row=None")
    else:
        print(
            f"{root}/{rel}: failed_attempts={row.failed_attempts} "
            f"locked={bool(row.locked)} generation={row.generation}"
        )
