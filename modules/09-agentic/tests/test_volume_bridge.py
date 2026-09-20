import pytest

from agentic import volume_bridge


@pytest.fixture(autouse=True)
def _reset_bridge():
    volume_bridge._get_volume = None
    volume_bridge._set_volume = None
    yield
    volume_bridge._get_volume = None
    volume_bridge._set_volume = None


def test_get_volume_raises_before_bind():
    with pytest.raises(volume_bridge.VolumeBridgeError, match="not available yet"):
        volume_bridge.get_volume()


def test_set_volume_raises_before_bind():
    with pytest.raises(volume_bridge.VolumeBridgeError, match="not available yet"):
        volume_bridge.set_volume(0.5)


def test_bind_wires_get_and_set_through():
    state = {"volume": 1.0}

    volume_bridge.bind(get_volume=lambda: state["volume"], set_volume=lambda v: state.update(volume=v))

    assert volume_bridge.get_volume() == 1.0
    volume_bridge.set_volume(0.3)
    assert state["volume"] == 0.3
    assert volume_bridge.get_volume() == 0.3
