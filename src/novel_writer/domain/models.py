"""Backward-compatible re-export shim.

All domain models now live in sub-modules (base, character, world, plot,
story, state, approval). This file re-exports them so that existing
`from novel_writer.domain.models import X` imports continue to work.
"""
from novel_writer.domain import *  # noqa: F401,F403
from novel_writer.domain import __all__  # noqa: F401
