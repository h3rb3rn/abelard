"""Tests for StateManager (Valkey-backed key operations)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.state_manager import StateManager


@pytest.fixture
def mock_valkey() -> MagicMock:
    """Return a fully async-mocked Valkey client."""
    m = MagicMock()
    m.set = AsyncMock(return_value=True)
    m.get = AsyncMock(return_value="0")
    m.incr = AsyncMock(return_value=1)
    m.incrby = AsyncMock(return_value=1)
    m.incrbyfloat = AsyncMock(return_value=1.0)
    m.delete = AsyncMock(return_value=1)
    m.close = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_initialize_sets_keys(mock_valkey: MagicMock) -> None:
    sm = StateManager("redis://localhost:6379/0")
    with patch.object(sm, '_valkey', mock_valkey):
        await sm.initialize()
        calls = [c[0][0] for c in mock_valkey.set.call_args_list]
        assert sm.KEY_ACTIVE in calls
        assert sm.KEY_COST_CENTRAL in calls


@pytest.mark.asyncio
async def test_is_active_returns_false_when_not_set(mock_valkey: MagicMock) -> None:
    mock_valkey.get = AsyncMock(return_value="false")  # type: ignore[attr-defined]
    sm = StateManager("redis://localhost:6379/0")
    with patch.object(sm, '_valkey', mock_valkey):
        assert await sm.is_active() is False


@pytest.mark.asyncio
async def test_global_killswitch_overrides_session(mock_valkey: MagicMock) -> None:
    """Globaler Kill-Switch stoppt auch eine aktive Session."""
    from services.state_manager import GLOBAL_KILLSWITCH_KEY

    async def fake_get(key):
        return "true"  # sowohl global kill als auch session active

    mock_valkey.get = AsyncMock(side_effect=fake_get)
    sm = StateManager("redis://localhost:6379/0", session_id="s1")
    with patch.object(sm, '_valkey', mock_valkey):
        assert await sm.is_active() is False


def test_keys_are_session_scoped() -> None:
    sm1 = StateManager("redis://localhost:6379/0", session_id="a")
    sm2 = StateManager("redis://localhost:6379/0", session_id="b")
    assert sm1.KEY_COST_CENTRAL != sm2.KEY_COST_CENTRAL
    assert sm1.KEY_TURN_COUNTER != sm2.KEY_TURN_COUNTER
    assert sm1.KEY_ACTIVE != sm2.KEY_ACTIVE


@pytest.mark.asyncio
async def test_add_cost_and_get(mock_valkey: MagicMock) -> None:
    mock_valkey.get = AsyncMock(return_value="10.5")  # type: ignore[attr-defined]
    mock_valkey.set = AsyncMock(return_value=True)
    sm = StateManager("redis://localhost:6379/0")
    with patch.object(sm, '_valkey', mock_valkey):
        cost = await sm.get_total_cost()
        assert cost == pytest.approx(10.5)
        await sm.add_cost(2.3)
        assert isinstance(cost, float)


@pytest.mark.asyncio
async def test_increment_turn_counter(mock_valkey: MagicMock) -> None:
    mock_valkey.incr = AsyncMock(return_value=5)  # type: ignore[attr-defined]
    sm = StateManager("redis://localhost:6379/0")
    with patch.object(sm, '_valkey', mock_valkey):
        counter = await sm.increment_turn_counter()
        assert counter == 5


@pytest.mark.asyncio
async def test_raises_before_initialize() -> None:
    sm = StateManager("redis://localhost:6379/0")
    with pytest.raises(RuntimeError, match="not initialized"):
        await sm.is_active()
