"""The ``wedge_captures`` repository package (D10 / A3 / T12).

Exposes the model and the repository, mirroring
``daemon/repositories/instance_ops/``.
"""

from .models import WedgeCapture
from .repository import WedgeCaptureRepository

__all__ = ["WedgeCapture", "WedgeCaptureRepository"]
