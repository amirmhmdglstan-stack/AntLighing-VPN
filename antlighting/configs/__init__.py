"""Configuration parsing, naming and ranking."""

from .models import SCHEME_INFO, ServerConfig
from .naming import friendly_name, flag_for, normalise_remark
from .parser import ParseResult, extract_links, parse_link, parse_text
from .ranking import ScoredServer, rank_servers, score_server, select_best

__all__ = [
    "SCHEME_INFO",
    "ParseResult",
    "ScoredServer",
    "ServerConfig",
    "extract_links",
    "flag_for",
    "friendly_name",
    "normalise_remark",
    "parse_link",
    "parse_text",
    "rank_servers",
    "score_server",
    "select_best",
]
