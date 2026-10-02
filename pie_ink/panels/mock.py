"""No hardware: keeps the last frame for the preview."""
from .base import Panel


class MockEInk(Panel):
    name = "mock"
    label = "None (preview only)"
    min_interval = 0.5

    def _open(self): pass
    def _close(self, clear): pass
    def _push(self, image, full): pass
    def _clear(self, dark): pass


class MockEInk27(MockEInk):
    name = "mock27"
    label = 'None (2.7" size)'
    width, height = 264, 176
    key_count = 4


class MockEInk37(MockEInk):
    name = "mock37"
    label = 'None (3.7" size)'
    width, height = 480, 280
