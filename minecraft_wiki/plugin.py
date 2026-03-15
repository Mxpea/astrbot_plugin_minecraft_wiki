"""Compatibility shim.

The real AstrBot Star class is defined in astrbot_plugin_minecraft_wiki.py.
"""

try:
    from ..astrbot_plugin_minecraft_wiki import MinecraftWikiPlugin
except ImportError:
    from astrbot_plugin_minecraft_wiki import MinecraftWikiPlugin

__all__ = ["MinecraftWikiPlugin"]
