"""
Media Player Scrobbler for SIMKL package.
"""

__version__ = "2.4.1"
__author__ = "kavin"

# Apply compatibility patches first, before any other imports
import simkl_mps.compatibility_patches
simkl_mps.compatibility_patches.apply_patches()

_LAZY_EXPORTS = {
    "SimklScrobbler": ("simkl_mps.main", "SimklScrobbler"),
    "run_as_background_service": ("simkl_mps.main", "run_as_background_service"),
    "main": ("simkl_mps.main", "main"),
    "run_tray_app": ("simkl_mps.tray_win", "run_tray_app"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name):
    """Load application entrypoints only when callers explicitly request them."""
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
