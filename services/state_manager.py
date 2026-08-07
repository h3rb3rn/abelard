"""Valkey-backed state management: kill-switch, cost tracking, session counters.

Alle Keys sind pro Session gescoped (``debate:{session_id}:...``), damit parallele
Debatten sich nicht gegenseitig Kosten, Zaehler oder den Kill-Switch ueberschreiben.
Zusaetzlich existiert ein globaler Kill-Switch (``debate:killswitch:global``), der
alle Sessions gleichzeitig stoppen kann.
"""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as valkey  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

GLOBAL_KILLSWITCH_KEY = "debate:killswitch:global"


class StateManager:
    """Async-safe manager over Valkey keys used by the debate orchestrator."""

    def __init__(self, url: str, session_id: str = "") -> None:
        self.url = url
        self.session_id = session_id or "default"
        prefix = f"debate:{self.session_id}"
        self.KEY_ACTIVE = f"{prefix}:status:active"
        self.KEY_COST_CENTRAL = f"{prefix}:cost:central"
        self.KEY_TOKENS_CENTRAL = f"{prefix}:tokens:central"
        self.KEY_TURN_COUNTER = f"{prefix}:counter:turn"
        self._valkey: valkey.client.Redis | None = None

    async def initialize(self) -> None:
        """Connect to Valkey and activate the debate session (idempotent)."""
        if self._valkey is None:
            self._valkey = valkey.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=5,
                retry_on_timeout=True,
            )
        await self._valkey.set(self.KEY_ACTIVE, "true")
        await self._valkey.set(self.KEY_COST_CENTRAL, "0.0")
        await self._valkey.set(self.KEY_TOKENS_CENTRAL, "0")
        await self._valkey.set(self.KEY_TURN_COUNTER, "0")
        logger.info("Valkey state initialized — session %s active", self.session_id)

    async def close(self) -> None:
        if self._valkey:
            await self._valkey.close()  # type: ignore[attr-defined]
            self._valkey = None

    # -- Kill-switch ------------------------------------------------------------

    async def activate_debate(self) -> None:
        await self._check()
        await self._valkey.set(self.KEY_ACTIVE, "true")  # type: ignore[union-attr]
        logger.info("Debate %s activated via kill-switch", self.session_id)

    async def deactivate_debate(self) -> None:
        await self._check()
        await self._valkey.set(self.KEY_ACTIVE, "false")  # type: ignore[union-attr]
        logger.warning("Debate %s deactivated via kill-switch", self.session_id)

    async def is_active(self) -> bool:
        """Session ist aktiv, sofern weder Session- noch globaler Kill-Switch greift."""
        await self._check()
        global_kill = await self._valkey.get(GLOBAL_KILLSWITCH_KEY)  # type: ignore[union-attr]
        if global_kill == "true":
            return False
        val = await self._valkey.get(self.KEY_ACTIVE)  # type: ignore[union-attr]
        return val == "true"

    # -- Cost tracking ----------------------------------------------------------

    async def get_total_cost(self) -> float:
        await self._check()
        val = await self._valkey.get(self.KEY_COST_CENTRAL)  # type: ignore[union-attr]
        return float(val or "0.0")

    async def add_cost(self, amount_usd: float) -> None:
        await self._check()
        await self._valkey.incrbyfloat(self.KEY_COST_CENTRAL, amount_usd)  # type: ignore[union-attr]

    async def get_total_tokens(self) -> int:
        await self._check()
        val = await self._valkey.get(self.KEY_TOKENS_CENTRAL)  # type: ignore[union-attr]
        return int(val or "0")

    async def add_tokens(self, count: int) -> None:
        await self._check()
        await self._valkey.incrby(self.KEY_TOKENS_CENTRAL, count)  # type: ignore[union-attr]

    # -- Turn counter -----------------------------------------------------------

    async def get_turn_counter(self) -> int:
        await self._check()
        val = await self._valkey.get(self.KEY_TURN_COUNTER)  # type: ignore[union-attr]
        return int(val or "0")

    async def increment_turn_counter(self) -> int:
        await self._check()
        counter = await self._valkey.incr(self.KEY_TURN_COUNTER)  # type: ignore[union-attr]
        assert isinstance(counter, int)
        return counter

    # -- Housekeeping -----------------------------------------------------------

    async def reset_session(self) -> None:
        """Delete all debate keys — use between sessions."""
        await self._check()
        keys = [self.KEY_ACTIVE, self.KEY_COST_CENTRAL, self.KEY_TOKENS_CENTRAL, self.KEY_TURN_COUNTER]  # noqa: SIM910
        await asyncio.gather(*(self._valkey.delete(k) for k in keys))  # type: ignore[union-attr]

    async def _check(self) -> None:
        if self._valkey is None:
            raise RuntimeError("StateManager not initialized — call initialize() first")


async def debounce_wait(valkey_client: valkey.Valkey, key: str) -> None:
    """Block the asyncio event loop until a Valkey key is set to 'true'.

    Useful for wait-on-startup or signal-wait patterns without busy-polling.
    """
    while True:
        val = await valkey_client.get(key)  # type: ignore[attr-defined]
        if val == "true":
            return
        await asyncio.sleep(0.5)
