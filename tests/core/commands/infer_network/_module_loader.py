from __future__ import annotations

import importlib
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def load_infer_network_module():
    module_name = "andrea.core.commands.infer_network"
    if module_name in sys.modules:
        return sys.modules[module_name]
    return importlib.import_module(module_name)
