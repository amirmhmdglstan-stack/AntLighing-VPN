"""Core abstraction, Xray backend and configuration generation."""

from .base import CoreBackend, CoreError, CoreState, CoreStatus
from .config_builder import (
    CoreOptions,
    LocalEndpoint,
    TunOptions,
    build_config,
    build_config_json,
    validate_structure,
)
from .manager import CoreManager
from .xray import XrayCore

__all__ = [
    "CoreBackend",
    "CoreError",
    "CoreManager",
    "CoreOptions",
    "CoreState",
    "CoreStatus",
    "LocalEndpoint",
    "TunOptions",
    "XrayCore",
    "build_config",
    "build_config_json",
    "validate_structure",
]
