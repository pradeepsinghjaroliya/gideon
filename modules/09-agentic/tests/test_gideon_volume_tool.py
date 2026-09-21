import pytest

from agentic import volume_bridge
from agentic.tools.gideon_volume_tool import GideonVolumeError, get_gideon_volume, set_gideon_volume


@pytest.fixture(autouse=True)
def _reset_bridge():
    volume_bridge._get_volume = None
    volume_bridge._set_volume = None
    yield
    volume_bridge._get_volume = None
    volume_bridge._set_volume = None


def _bind(initial: float = 1.0):
    state = {"volume": initial}
    volume_bridge.bind(get_volume=lambda: state["volume"], set_volume=lambda v: state.update(volume=v))
    return state


def test_get_gideon_volume_reads_through_bridge_as_percent():
    _bind(0.5)

    assert get_gideon_volume() == 50


def test_set_gideon_volume_writes_through_bridge_as_fraction():
    state = _bind(1.0)

    result = set_gideon_volume(30)

    assert state["volume"] == pytest.approx(0.3)
    assert result == "Assistant voice volume set to 30%."


@pytest.mark.parametrize("percent", [-1, 101, 1000])
def test_set_gideon_volume_rejects_out_of_range_percent(percent):
    state = _bind(1.0)

    with pytest.raises(GideonVolumeError, match="between 0 and 100"):
        set_gideon_volume(percent)

    assert state["volume"] == 1.0


def test_get_gideon_volume_raises_before_bridge_bound():
    with pytest.raises(volume_bridge.VolumeBridgeError, match="not available yet"):
        get_gideon_volume()


def test_set_gideon_volume_raises_before_bridge_bound():
    with pytest.raises(volume_bridge.VolumeBridgeError, match="not available yet"):
        set_gideon_volume(50)
