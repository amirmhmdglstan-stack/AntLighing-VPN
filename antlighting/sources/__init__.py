"""Configuration source plug-ins and the built-in source catalogue."""

from __future__ import annotations

from typing import Any

from .base import (
    DEFAULT_TIMEOUT,
    ConfigSource,
    FetchResult,
    fetch_all,
    http_get,
    register,
    source_kinds,
)
from .github import GitHubSource, parse_repo_spec
from .localfile import HTTPSource, LocalFileSource
from .telegram import TelegramSource, extract_channel

__all__ = [
    "ConfigSource",
    "FetchResult",
    "GitHubSource",
    "HTTPSource",
    "LocalFileSource",
    "TelegramSource",
    "create_source",
    "default_source_specs",
    "extract_channel",
    "fetch_all",
    "http_get",
    "parse_repo_spec",
    "register",
    "source_kinds",
    "DEFAULT_TIMEOUT",
]


def create_source(spec: dict[str, Any]) -> ConfigSource | None:  # noqa: F811
    from .base import create_source as _create

    return _create(spec)


def default_source_specs() -> list[dict[str, Any]]:
    """The sources AntLighting ships with.

    These are *defaults a user can edit*, not hard dependencies.  Any of them
    can disappear, change format or go offline and the application keeps
    working with whatever it can reach.
    """
    return [
        {
            "id": "github-free-v2ray-configs-verified",
            "kind": "github",
            "label": "Free V2Ray Configs (verified)",
            "enabled": True,
            "urls": [
                "https://github.com/0xRadikal/Free-v2ray-Configs/blob/main/verified/configs.txt"
            ],
        },
        {
            "id": "github-free-v2ray-configs-all",
            "kind": "github",
            "label": "Free V2Ray Configs (all)",
            "enabled": False,  # ~3 MB, mostly duplicates of "verified"
            "urls": [
                "https://github.com/0xRadikal/Free-v2ray-Configs/blob/main/all/configs.txt"
            ],
        },
        {
            "id": "telegram-public-channels",
            "kind": "telegram",
            "label": "Public Telegram channels",
            "enabled": True,
            "urls": [
                "@irconfig",
                "@IRAN_V2RAY1",
                "@spdnet",
                "@V2ray_Alpha",
                "@V2rayNG3",
            ],
            "options": {"pages": 3},
        },
    ]
