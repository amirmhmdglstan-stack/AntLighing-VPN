"""Server ranking for automatic server selection.

The requirement is that a *reliable* server wins over a merely *fast* one:
"a very low-latency server that immediately fails should not beat a slightly
slower but stable server".  The score is therefore dominated by status, then
reliability, and only then by latency.

Ordering, best first:

1. working + low latency + proven reliability
2. working, less proven
3. never tested (unknown) — worth trying when nothing is proven yet
4. slow (works but above the latency ceiling)
5. failed / timed out
6. invalid / unsupported
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..configs.models import ServerConfig

STATUS_BASE: dict[str, float] = {
    "working": 1000.0,
    "unknown": 420.0,
    "testing": 380.0,
    "slow": 220.0,
    "failed": -400.0,
    "timeout": -500.0,
    "unavailable": -600.0,
    "invalid": -1000.0,
}

# A result older than this is treated as increasingly unreliable.
STALE_AFTER_SECONDS = 6 * 3600
STALE_HALF_LIFE = 24 * 3600

# Statuses that automatic selection is allowed to dial.  ``failed``/``timeout``
# servers are deliberately absent: retrying one that just failed repeatedly is
# worse than telling the user nothing worked.
SELECTABLE_STATUS = frozenset({"working", "slow", "unknown", "testing"})

# Protocols that tend to be the most robust on restricted networks.
DEFAULT_PROTOCOL_PREFERENCE: tuple[str, ...] = ("vless", "trojan", "vmess", "ss", "hysteria2")


@dataclass(slots=True)
class ScoredServer:
    config: ServerConfig
    score: float
    latency_ms: float | None
    status: str
    reliability: float
    tested_count: int
    consecutive_failures: int
    age_seconds: float | None
    reason: str

    @property
    def identity(self) -> str:
        return self.config.identity

    @property
    def usable(self) -> bool:
        return self.status in SELECTABLE_STATUS and self.config.usable

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": self.config.identity,
            "name": self.config.display_name(),
            "scheme": self.config.scheme,
            "country": self.config.country,
            "country_code": self.config.country_code,
            "address": self.config.address,
            "port": self.config.port,
            "score": round(self.score, 2),
            "latency_ms": self.latency_ms,
            "status": self.status,
            "reliability": round(self.reliability, 3),
            "tested_count": self.tested_count,
            "age_seconds": self.age_seconds,
            "reason": self.reason,
        }


def _reliability(success: int, failure: int) -> float:
    total = success + failure
    if total <= 0:
        return 0.5  # unknown: neutral
    return success / total


def score_server(
    config: ServerConfig,
    metrics: dict[str, Any] | None = None,
    *,
    now: float | None = None,
    test_timeout: float = 8.0,
    slow_threshold_ms: float = 900.0,
    preferred_protocols: Sequence[str] = DEFAULT_PROTOCOL_PREFERENCE,
) -> ScoredServer:
    """Compute a ranking score for one server."""
    metrics = metrics or {}
    latency = metrics.get("latency_ms")
    latency = float(latency) if latency not in (None, "") else None
    status = str(metrics.get("status") or "unknown")
    success = int(metrics.get("success_count") or 0)
    failure = int(metrics.get("failure_count") or 0)
    consecutive = int(metrics.get("consecutive_failures") or 0)
    last_tested = metrics.get("last_tested")

    age: float | None = None
    if now is not None and last_tested:
        age = max(0.0, float(now) - float(last_tested))

    if not config.usable:
        status = "invalid"

    score = STATUS_BASE.get(status, 0.0)
    parts: list[str] = [status]

    # --- latency ------------------------------------------------------------
    if latency is not None and status == "working":
        ceiling = max(test_timeout * 1000.0, 1.0)
        fraction = min(max(latency, 0.0), ceiling) / ceiling
        latency_bonus = 300.0 * (1.0 - fraction)
        if latency > slow_threshold_ms:
            latency_bonus -= 120.0
            parts.append("slow")
        score += latency_bonus
        parts.append(f"{latency:.0f}ms")
    elif latency is not None and status == "slow":
        score -= min(latency, 5000.0) / 20.0

    # --- reliability --------------------------------------------------------
    reliability = _reliability(success, failure)
    tested = success + failure
    if tested:
        # Wilson-ish shrinkage: with few samples, stay near neutral.
        weight = tested / (tested + 3.0)
        shrunk = 0.5 + (reliability - 0.5) * weight
        score += 180.0 * (shrunk - 0.5)
        parts.append(f"{tested} tests")
        if consecutive:
            penalty = 70.0 * min(consecutive, 5)
            score -= penalty
            parts.append(f"{consecutive} fails in a row")
    else:
        reliability = 0.5

    # --- freshness ----------------------------------------------------------
    if age is not None:
        if age > STALE_AFTER_SECONDS:
            decay = math.pow(0.5, (age - STALE_AFTER_SECONDS) / STALE_HALF_LIFE)
            # Shrink the *informative* part of the score towards the neutral
            # "unknown" level rather than deleting the result entirely.
            neutral = STATUS_BASE["unknown"]
            score = neutral + (score - neutral) * (0.35 + 0.65 * decay)
            parts.append("stale")

    # --- protocol preference ------------------------------------------------
    # A deliberate tie-breaker only.  It is small relative to the latency term
    # (max 8 points against a 300 point spread) so it settles near-identical
    # candidates without ever overriding a real measured latency difference.
    if preferred_protocols:
        try:
            rank = list(preferred_protocols).index(config.scheme)
        except ValueError:
            rank = len(preferred_protocols)
        score += 12.0 - 2.0 * rank

    return ScoredServer(
        config=config,
        score=score,
        latency_ms=latency,
        status=status,
        reliability=reliability,
        tested_count=tested,
        consecutive_failures=consecutive,
        age_seconds=age,
        reason=" · ".join(parts),
    )


def rank_servers(
    configs: Iterable[ServerConfig],
    metrics_lookup,
    *,
    now: float | None = None,
    test_timeout: float = 8.0,
    slow_threshold_ms: float = 900.0,
    preferred_protocols: Sequence[str] = DEFAULT_PROTOCOL_PREFERENCE,
    exclude_status: frozenset[str] = frozenset({"invalid"}),
) -> list[ScoredServer]:
    """Score and sort servers, best first.

    ``metrics_lookup`` is a callable ``identity -> dict`` (the store, or a dict
    in tests).  Ties are broken by identity so the order is deterministic.
    """
    scored: list[ScoredServer] = []
    for config in configs:
        metrics = metrics_lookup(config.identity) if callable(metrics_lookup) else (
            metrics_lookup.get(config.identity)
        )
        entry = score_server(
            config,
            metrics or {},
            now=now,
            test_timeout=test_timeout,
            slow_threshold_ms=slow_threshold_ms,
            preferred_protocols=preferred_protocols,
        )
        if entry.status in exclude_status:
            continue
        scored.append(entry)

    scored.sort(key=lambda item: (-item.score, item.config.identity))
    return scored


def select_best(
    configs: Iterable[ServerConfig],
    metrics_lookup,
    *,
    skip_identities: Iterable[str] = (),
    allow_unknown: bool = True,
    **kwargs: Any,
) -> ScoredServer | None:
    """Pick the best server worth dialling.

    Servers already known to be failing are never returned — the caller should
    surface "couldn't find a working server" rather than silently retry a server
    that has just failed several times.
    """
    skip = set(skip_identities)
    ranked = rank_servers(configs, metrics_lookup, **kwargs)
    for entry in ranked:
        if entry.identity in skip:
            continue
        if not entry.config.usable:
            continue
        if entry.status not in SELECTABLE_STATUS:
            continue
        if entry.status == "unknown" and not allow_unknown:
            continue
        return entry
    return None
