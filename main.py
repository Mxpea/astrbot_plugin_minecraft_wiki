from pathlib import Path
import sys


PLUGIN_ROOT = Path(__file__).resolve().parent
plugin_root_str = str(PLUGIN_ROOT)
if plugin_root_str not in sys.path:
	sys.path.insert(0, plugin_root_str)


from minecraft_wiki.plugin import MinecraftWikiPlugin

__all__ = ["MinecraftWikiPlugin"]
