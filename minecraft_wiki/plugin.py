"""Compatibility shim.

The real AstrBot Star class is defined in main.py to avoid older-core loading issues.
"""

try:
    from ..main import MinecraftWikiPlugin
except ImportError:
    from main import MinecraftWikiPlugin

__all__ = ["MinecraftWikiPlugin"]
