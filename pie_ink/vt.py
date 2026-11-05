"""Keeping the Linux console off a screen PiE-ink draws on itself (the GPi Case 2W's).

The case's screen is the Pi's framebuffer, which the console draws on too: a login prompt, a
blinking cursor, messages at boot. So while PiE-ink runs it has a console of its own — tty7 —
switched to and put in graphics mode, where the console draws nothing; when it stops, that
console goes back to text and tty1 comes back.

That takes root, so PiE-ink's service has it done before it starts and after it stops (the
drop-in setup.sh writes for the GPi Case):

    python3 pie_ink/vt.py graphics      # before
    python3 pie_ink/vt.py text          # after

Run by hand as root, the panel does it itself. As anyone else (python3 app.py over SSH, say) it
can't switch consoles, so it hides the console's cursor instead — the blinking block that would
show through — by writing to the console in front, which works when that's yours (signed in on
it) or you're in the tty group (setup.sh adds it); the cursor comes back when PiE-ink stops.
"""
import fcntl
import os
import sys

VT = 7                      # PiE-ink's console
HOME_VT = 1                 # the login console, given back
KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0, 1
VT_ACTIVATE, VT_WAITACTIVE = 0x5606, 0x5607
BLINK = "/sys/class/graphics/fbcon/cursor_blink"
ACTIVE = "/sys/class/tty/tty0/active"
HIDE, SHOW = b"\033[?25l\033[?1c", b"\033[?25h\033[?0c"     # the cursor off (and the Linux console's own way), and back


def _blink(on):
    try:
        with open(BLINK, "w") as f:
            f.write("1" if on else "0")
    except OSError:
        pass


def _front():
    """The console in front now ("/dev/tty1"...)."""
    try:
        with open(ACTIVE) as f:
            name = f.read().strip()
        if name.startswith("tty") and name[3:].isdigit():
            return f"/dev/{name}"
    except OSError:
        pass
    return f"/dev/tty{HOME_VT}"


def _write(path, data):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(fd, data)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def hide_cursor():
    """The console's cursor off, without root: True if it could be."""
    _blink(False)
    return _write(_front(), HIDE)


def show_cursor():
    _blink(True)
    _write(_front(), SHOW)


def _tty(n):
    return os.open(f"/dev/tty{n}", os.O_RDWR | os.O_NOCTTY)


def take(quiet=False):
    """tty7, in graphics mode, in front. True if done (it needs root)."""
    try:
        fd = _tty(VT)
    except OSError as e:
        if not quiet:
            print(f"can't take the console: {e}", file=sys.stderr)
        return False
    try:
        fcntl.ioctl(fd, VT_ACTIVATE, VT)
        try:
            fcntl.ioctl(fd, VT_WAITACTIVE, VT)
        except OSError:
            pass
        fcntl.ioctl(fd, KDSETMODE, KD_GRAPHICS)
        _blink(False)
        return True
    except OSError as e:
        if not quiet:
            print(f"can't take the console: {e}", file=sys.stderr)
        return False
    finally:
        os.close(fd)


def give(quiet=False):
    """tty7 back to text, and the login console in front again."""
    try:
        fd = _tty(VT)
    except OSError as e:
        if not quiet:
            print(f"can't give the console back: {e}", file=sys.stderr)
        return False
    try:
        fcntl.ioctl(fd, KDSETMODE, KD_TEXT)
        fcntl.ioctl(fd, VT_ACTIVATE, HOME_VT)
        _blink(True)
        return True
    except OSError as e:
        if not quiet:
            print(f"can't give the console back: {e}", file=sys.stderr)
        return False
    finally:
        os.close(fd)


if __name__ == "__main__":
    what = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if what == "graphics":
        take()
    elif what == "text":
        give()
    else:
        print("usage: vt.py graphics | text", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)                                     # (best effort: the service starts either way)
