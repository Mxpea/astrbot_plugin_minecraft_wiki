"""Compatibility shim.

Canonical plugin entry is main.py.
"""

try:
    from .main import MinecraftWikiPlugin
except ImportError:
    from main import MinecraftWikiPlugin

__all__ = ["MinecraftWikiPlugin"]
