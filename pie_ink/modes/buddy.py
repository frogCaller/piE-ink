"""The little friend's face: big, in the middle, with what it last said
underneath. It looks the way the camera looks, lights up when it sees you,
and is surprised when something's new (pie_ink/buddy.py does the seeing)."""
from ..text import draw_block, font, text_width
from .base import Mode


class BuddyMode(Mode):
    name = "buddy"
    label = "Friend"
    interval = 1.0

    def render(self):
        from .. import buddy
        from .simple import MessageMode
        f = self.frame()
        w, h = f.width, f.height
        line = buddy.BUDDY.recent_line(600)
        # the face as big as fits: most of the width, about half the height with words under it
        room_h = h * (0.5 if line else 0.62)
        size = max(14, int(room_h * 0.8))
        face_font = font(2, size)
        face, mood, caption = buddy.BUDDY.drawable_face(face_font)
        while size > 14 and text_width(f.draw, face, face_font) > w * 0.86:
            size -= 2
            face_font = font(2, size)
        face, mood, caption = buddy.BUDDY.drawable_face(face_font)
        top = max(2, int((room_h - size) / 2)) if line else max(2, int((h * 0.78 - size) / 2))
        draw_block(f.draw, face, face_font, (0, top, w, top + size * 1.3), f.fg, align="center", valign="top", pad=0)
        below = top + int(size * 1.25)
        if line:
            box = (4, below, w - 4, h - 2)
            fitted = MessageMode._fit_font(f, line, box, {"font": self.settings.get("font", "Font.ttc")},
                                           cap=max(12, int(h * 0.16)))
            draw_block(f.draw, line, fitted, box, f.fg, align="center", valign="top")
        else:
            small = font(1, max(11, int(h * 0.1)))
            draw_block(f.draw, caption, small, (0, below, w, h - 2), f.fg, align="center", valign="top", pad=0)
        return f.image
