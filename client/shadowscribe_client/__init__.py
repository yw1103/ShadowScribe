"""ShadowScribe desktop client — the ``ss`` CLI.

Light by design: ``httpx`` is the only hard dependency, so a laptop never has to
install PyTorch just to be told what happened in the meeting.
"""

__version__ = "0.1.0"

from .api import ShadowScribeClient, ShadowScribeError
from .config import ClientConfig

__all__ = ["ClientConfig", "ShadowScribeClient", "ShadowScribeError", "__version__"]
