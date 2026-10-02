#!/usr/bin/env python3
"""Render every mode at every panel size with no hardware, and report.

    python3 selftest.py
"""
import sys
import time

from pie_ink import settings as cfg
from pie_ink.modes import MODES
from pie_ink.panels import PANELS, to_bilevel, to_png


def main():
    config = cfg.load()
    config["display"]["driver"] = "mock"
    sizes = sorted({(p.width, p.height) for p in PANELS.values()} | {(280, 240)})   # the Whisplay, sideways
    failed = 0
    for w, h in sizes:
        panel = {"width": w, "height": h, "kind": "eink"}
        for name, cls in MODES.items():
            t = time.time()
            try:
                mode = cls(config, panel)
                img = mode.render()
                to_bilevel(img, (w, h))
                to_png(img, (w, h))
                print(f"  ok   {w}x{h:<4} {name:8s} {(time.time() - t) * 1000:6.1f} ms")
            except Exception as e:
                failed += 1
                print(f"  FAIL {w}x{h:<4} {name:8s} {type(e).__name__}: {e}")
    # the colour versions the Whisplay's screen uses, both ways round
    from pie_ink import lcd_screens
    for w, h in ((280, 240), (240, 280)):
        for name in ("system", "weather", "crypto", "gps", "camera", "paint", "clock", "testcard", "map", "buddy"):
            t = time.time()
            try:
                img = lcd_screens.render({"screen": name, "light_mode": True, "accent": "#62d2ff"}, config=config, size=(w, h))
                assert img.size == (w, h), img.size
                to_png(img, (w, h))
                print(f"  ok   {w}x{h:<4} {name:8s} {(time.time() - t) * 1000:6.1f} ms  (colour)")
            except Exception as e:
                failed += 1
                print(f"  FAIL {w}x{h:<4} {name:8s} {type(e).__name__}: {e}  (colour)")
    print("all good" if not failed else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
