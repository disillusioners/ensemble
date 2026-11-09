"""rk_session.py — RE-KILL 2: mint a REAL ensemble_sid session row.

In-process INSERT into the dev DB's auth_sessions table (the
"in-process repository insert" pattern the commission names). The
session is bound to the loopback /24 (127.0.0.0/24) so the
auth lane's ip-subnet fingerprint matches the probe peer, and it
expires in 1h. The id is written to the fence for the probe
scripts; it is a drill credential on a DEV database.
"""
import datetime
import os
import secrets
import sys

from sqlmodel import Session, SQLModel  # noqa: F401  (SQLModel import for metadata)

from sqlalchemy import create_engine

from daemon.repositories.auth_session.models import AuthSession

url = (
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:"
    f"{os.environ['POSTGRES_PASSWORD']}@{os.environ['POSTGRES_HOST']}:"
    f"{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"
)
engine = create_engine(url)

sid = secrets.token_urlsafe(32)
now = datetime.datetime.now(datetime.timezone.utc)
now_iso = now.replace(microsecond=0).isoformat()
exp_iso = (
    now + datetime.timedelta(hours=1)
).replace(microsecond=0).isoformat()

row = AuthSession(
    id=sid,
    subject="rekill2-drill-operator",
    email="rekill2@drill.invalid",
    scopes="",
    auth_method="session",
    user_agent="rekill2-tester",
    ip_subnet="127.0.0.0/24",
    created_at=now_iso,
    last_used_at=None,
    expires_at=exp_iso,
)
with Session(engine) as s:
    s.add(row)
    s.commit()

# read-back proof
with Session(engine) as s:
    got = s.get(AuthSession, sid)
    if got is None:
        print("SESSION INSERT FAILED")
        sys.exit(1)
    print(
        f"session minted: subject={got.subject} expires_at={got.expires_at} "
        f"ip_subnet={got.ip_subnet} auth_method={got.auth_method}"
    )
open(os.path.join(os.environ["FENCE"], "session-id"), "w").write(sid)
print("== SESSION OK")
