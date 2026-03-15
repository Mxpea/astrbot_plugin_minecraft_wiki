try:
	from .minecraft_wiki.plugin import MinecraftWikiPlugin
except ImportError:
	# Fallback for local direct-file execution outside AstrBot package loader.
	from minecraft_wiki.plugin import MinecraftWikiPlugin

__all__ = ["MinecraftWikiPlugin"]
