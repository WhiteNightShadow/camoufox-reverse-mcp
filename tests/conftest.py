import pytest


@pytest.fixture
def browser_manager():
    """Create a fresh BrowserManager instance for testing."""
    from camoufox_reverse_mcp.browser import BrowserManager
    mgr = BrowserManager()
    yield mgr


@pytest.fixture
def sample_config():
    """Sample browser launch configuration."""
    return {
        "headless": True,
        "os": "windows",
        "humanize": False,
        "geoip": False,
        "block_images": False,
        "block_webrtc": False,
    }
