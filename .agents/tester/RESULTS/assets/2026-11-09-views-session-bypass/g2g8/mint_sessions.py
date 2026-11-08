#!/usr/bin/env python
"""g2g8 session mint — insert AuthSession rows directly into the dev DB
(mirrors AuthService.issue_session field population; the daemon validates
via AuthSessionRepository.get_session on the SAME DB). Mints:
  - ensemble_sid.valid    (24h TTL, operator scopes)
  - ensemble_sid.expired  (expired 1h ago — for G4)
Prints the cookie values; also writes them to $FENCE/sid-valid / sid-expired."""
import os, secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from daemon.repositories.auth_session.models import AuthSession
from daemon.repositories.auth_session.repository import AuthSessionRepository
from daemon.auth.scopes import SESSION_OPERATOR_SCOPES

url = (f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
       f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}")
engine = create_engine(url)
repo = AuthSessionRepository(engine)

now = datetime.now(timezone.utc)
def iso(dt):  # the sanctioned producer's format byte-for-byte
    return dt.isoformat()

granted = " ".join(sorted(set(SESSION_OPERATOR_SCOPES)))
rows = {}
for name, expires in (("sid-valid", now + timedelta(hours=24)),
                      ("sid-expired", now - timedelta(hours=1))):
    sid = secrets.token_urlsafe(32)
    row = AuthSession(
        id=sid,
        subject="drill-operator-g2g8",
        email="drill-operator@example.invalid",
        scopes=granted,
        auth_method="session",
        user_agent="g2g8-drill-mint",
        ip_subnet="127.0.0.0/24",  # fingerprint OFF by default; matches curl peer anyway
        created_at=iso(now),
        last_used_at=iso(now),
        expires_at=iso(expires),
    )
    repo.insert_session(row)
    rows[name] = sid
    open(os.path.join(os.environ["FENCE"], name), "w").write(sid)
    print(f"{name}={sid}  expires_at={iso(expires)}")
print(f"scopes={granted[:80]}...")
