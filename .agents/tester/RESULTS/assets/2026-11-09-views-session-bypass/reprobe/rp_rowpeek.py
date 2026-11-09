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
row = svc.get_row(os.environ["ROOT"], os.environ["VIEW_R"])
if row is None:
    print(f"rowpeek {os.environ['ROOT']}/{os.environ['VIEW_R']}: row=None")
else:
    print(
        f"rowpeek {os.environ['ROOT']}/{os.environ['VIEW_R']}: "
        f"failed_attempts={row.failed_attempts} "
        f"locked={bool(row.locked)} generation={row.generation}"
    )
