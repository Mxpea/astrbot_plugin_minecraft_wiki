__all__ = ["MinecraftWikiPlugin"]


def __getattr__(name: str):
	if name == "MinecraftWikiPlugin":
		from .plugin import MinecraftWikiPlugin

		return MinecraftWikiPlugin
	raise AttributeError(name)
