"""ShadowScribe server — passive ingestion + causal distillation for an ambient cognitive exocortex.

The server is deliberately split into two concerns:

* **sensory layer** (:mod:`shadowscribe.pipeline`) — turns raw ambient audio into
  speaker-attributed transcripts. It never speaks, never interrupts.
* **memory layer** (:mod:`shadowscribe.memory`) — distils transcripts into causal
  memories and hands them to the long-term store so the desktop agent can inherit
  context for free.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
