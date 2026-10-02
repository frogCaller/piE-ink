"""Finding the 40-pin header's GPIO chip with lgpio.

Every Pi exposes the header on a chip labelled pinctrl-* (bcm2835, bcm2711,
rp1). Its number differs between models and even kernel versions on the
Pi 5, so we look it up by label rather than guessing.
"""
import logging

log = logging.getLogger(__name__)


def open_header_chip(lgpio):
    """Return an lgpio chip handle for the header, or raise a clear error."""
    denied = False
    for n in range(16):
        try:
            h = lgpio.gpiochip_open(n)
        except lgpio.error as e:
            if "permission" in str(e).lower() or "denied" in str(e).lower():
                denied = True
            continue
        try:
            label = lgpio.gpio_get_chip_info(h)[-1]     # (status, lines, name, label)
            if isinstance(label, bytes):
                label = label.decode()
        except (lgpio.error, TypeError, IndexError):
            label = ""
        if str(label).startswith("pinctrl-"):
            log.info("using gpiochip%d (%s)", n, label)
            return h
        lgpio.gpiochip_close(h)
    if denied:
        raise RuntimeError("no permission for /dev/gpiochip*: add the user to the gpio group "
                           "(sudo usermod -aG gpio $USER) and log in again")
    raise RuntimeError("no GPIO chip with a pinctrl-* label found; is this a Raspberry Pi?")
