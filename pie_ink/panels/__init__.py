from .base import Panel, to_bilevel, to_png
from .eink import EInk27V1, EInk27V2, EInk37, EInk213V3, EInk213V4
from .mock import MockEInk, MockEInk27, MockEInk37
from .whisplay import Whisplay, MockWhisplay

PANELS = {p.name: p for p in (EInk213V4, EInk213V3, EInk27V2, EInk27V1, EInk37, Whisplay,
                              MockEInk, MockEInk27, MockEInk37, MockWhisplay)}


def make(display):
    """Build a panel from config["display"]."""
    cls = PANELS.get(display.get("driver"), MockEInk)
    panel = cls(rotation=display.get("rotation", 0), full_refresh_every=display.get("full_refresh_every", 60))
    if hasattr(panel, "brightness"):
        panel.brightness = max(0, min(100, int(display.get("brightness", 70) or 0)))
    return panel


def describe():
    return [{"id": p.name, "label": p.label, "kind": p.kind, "colour": getattr(p, "colour", False),
             "width": p.width, "height": p.height,
             "buttons": getattr(p, "key_count", 0) or len(getattr(p, "buttons", []))} for p in PANELS.values()]

__all__ = ["Panel", "to_bilevel", "to_png", "PANELS", "make", "describe"]
