"""Compatibility re-export for the canonical fundamentals package.

The historical module did not define ``__all__`` consistently, so the shim
copies every public name instead of narrowing the supported import surface.
"""

from .fundamentals import pit as _canonical

for _name, _value in vars(_canonical).items():
    if not _name.startswith("_"):
        globals()[_name] = _value

del _name, _value, _canonical
