"""Immutable registry for FlyMail V2 provider plugins."""

from __future__ import annotations

from functools import lru_cache
from types import MappingProxyType
from typing import Iterable

from flymail.providers.contracts import ProviderPlugin
from flymail.providers.plugins.generic import PLUGIN as GENERIC_PLUGIN
from flymail.providers.plugins.gmail import PLUGIN as GMAIL_PLUGIN
from flymail.providers.plugins.icloud import PLUGIN as ICLOUD_PLUGIN
from flymail.providers.plugins.netease import PLUGIN as NETEASE_PLUGIN
from flymail.providers.plugins.outlook import PLUGIN as OUTLOOK_PLUGIN
from flymail.providers.plugins.qq import PLUGIN as QQ_PLUGIN
from flymail.providers.plugins.sina import PLUGIN as SINA_PLUGIN


_DEFAULT_PLUGINS = (
    GENERIC_PLUGIN,
    GMAIL_PLUGIN,
    OUTLOOK_PLUGIN,
    QQ_PLUGIN,
    NETEASE_PLUGIN,
    ICLOUD_PLUGIN,
    SINA_PLUGIN,
)


class ProviderRegistry:
    def __init__(self, plugins: Iterable[ProviderPlugin]) -> None:
        ordered: list[tuple[str, ProviderPlugin]] = []
        seen: set[str] = set()
        for plugin in plugins:
            if not isinstance(plugin, ProviderPlugin):
                raise TypeError("plugin does not satisfy ProviderPlugin")
            key = str(plugin.key or "").strip().casefold()
            if not key:
                raise ValueError("provider key is required")
            if key in seen:
                raise ValueError(f"duplicate provider: {key}")
            seen.add(key)
            ordered.append((key, plugin))
        if not ordered:
            raise ValueError("provider registry must not be empty")
        self._keys = tuple(key for key, _plugin in ordered)
        self._plugins = MappingProxyType(dict(ordered))

    @classmethod
    @lru_cache(maxsize=1)
    def default(cls) -> "ProviderRegistry":
        return cls(_DEFAULT_PLUGINS)

    def keys(self) -> tuple[str, ...]:
        return self._keys

    def get(self, provider_key: str) -> ProviderPlugin:
        key = str(provider_key or "").strip().casefold()
        try:
            return self._plugins[key]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {key or '<empty>'}") from exc
