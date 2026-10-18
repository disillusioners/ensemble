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
mode = sys.argv[1] if len(sys.argv) > 1 else "both"
rels = ["target.md", "target5.md"] if mode == "both" else [mode]
for rel in rels:
    row = svc.get_row("bruteroot1", rel)
    if row is None:
        print(f"rowpeek {rel}: row=None")
    else:
        print(
            f"rowpeek {rel}: failed_attempts={row.failed_attempts} "
            f"locked={bool(row.locked)} generation={row.generation}"
        )
