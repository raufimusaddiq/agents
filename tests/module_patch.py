"""Patch shared imports consistently across the refactored server modules."""
from contextlib import ExitStack, contextmanager
import sys
from unittest.mock import patch

@contextmanager
def patch_shared(module, name, *args, **kwargs):
    original = getattr(module, name)
    targets = [module] + [value for key, value in list(sys.modules.items())
                          if key.startswith('board.') and value is not module
                          and (getattr(value, name, object()) is original
                               or (name.endswith('_PATH') and getattr(value, name, None) == original))]
    with ExitStack() as stack:
        replacement = stack.enter_context(patch.object(module, name, *args, **kwargs))
        for target in targets[1:]:
            stack.enter_context(patch.object(target, name, replacement))
        yield replacement
