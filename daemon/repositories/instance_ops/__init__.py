"""Instance-ops audit ledger (nest-ops D-2 / D-15).

``instance_ops_audit`` is the append-only ledger behind the
``instance_ops`` privileged tool category. It records every ACT — the
preview, the refusal, the write-ahead intent, and the committed
outcome — with the actor, the target, the reason and the provenance
fields that authorised it.

The model copies ``repair_log``'s vocabulary (``daemon/repositories/
ens_db/models.py``) minus the six SQL-repair-specific columns, so the
house audit shape is reused WITHOUT the cross-category coupling.
"""

from .models import InstanceOpsAudit
from .repository import InstanceOpsAuditRepository

__all__ = ["InstanceOpsAudit", "InstanceOpsAuditRepository"]
