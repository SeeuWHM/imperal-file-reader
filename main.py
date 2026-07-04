"""Entry point — module purge + import order, same pattern as every other
extension in this workspace (hot-reload + cross-extension sys.modules safety;
see doc-reader/main.py for the reference version of this comment)."""
from __future__ import annotations

import os
import sys

_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

_MODULES = (
    "app", "schemas", "schemas_sdl",
    "providers", "providers.helpers", "providers.extractor",
    "providers.lifecycle", "providers.content_ops", "providers.text_windows",
    "handlers_upload", "handlers_content", "handlers_files", "skeleton", "panels",
)
for _m in [k for k in sys.modules if k in _MODULES]:
    del sys.modules[_m]

from app import ext, chat  # noqa: E402, F401

import schemas_sdl        # noqa: E402, F401
import handlers_upload    # noqa: E402, F401
import handlers_content   # noqa: E402, F401
import handlers_files     # noqa: E402, F401
import skeleton           # noqa: E402, F401
import panels             # noqa: E402, F401
