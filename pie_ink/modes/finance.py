"""Finance: what your salary comes to, and how much of today you have earned.

Both numbers come from things you already set — the yearly income in Settings
and the work blocks in the Calendar — so there is no log to keep up to date.
"""
import logging
from datetime import date, datetime

from ..text import font, text_width
from .base import Mode
from .me import Day

log = logging.getLogger(__name__)
WEEKS = 52
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _money(v, sym="$", places=0):
    if v is None:
        return "--"
    if abs(v) >= 1e6:
        return f"{sym}{v / 1e6:.2f}M"
    if abs(v) >= 1e5:
        return f"{sym}{v / 1e3:.0f}K"
    if abs(v) >= 1e4:
        return f"{sym}{v / 1e3:.1f}K"
    return f"{sym}{v:,.{places}f}"


class FinanceMode(Mode):
    name = "finance"
    label = "Finance"

    @property
    def interval(self):
        return 2.0 if self.settings.get("show_today", True) else 30.0

    def _figures(self, day, now):
        salary = float(self.settings.get("salary") or 0)
        if salary <= 0:
            return None
        periods = max(1, int(self.settings.get("pay_periods", 52) or 52))
        workdays = [d.lower() for d in self.config.get("schedule", {}).get("workdays", [])] or DAYS[:5]
        daily = salary / (len(workdays) * WEEKS)
        paid_today = day.paid_minutes_per_day()
        earned = 0.0
        if day.workday and paid_today:
            earned = day.paid_minutes_so_far() / paid_today * daily
        jan1 = date(now.year, 1, 1)
        gone = sum(1 for i in range((now.date() - jan1).days)
                   if DAYS[(jan1.toordinal() + i - 1) % 7] in workdays)
        return {"salary": salary, "per_check": salary / periods, "daily": daily,
                "weekly": salary / WEEKS, "monthly": salary / 12,
                "earned": earned, "year": gone * daily + earned,
                "paid_today": paid_today, "workday": day.workday}

    def render(self):
        f = self.frame()
        d, fg = f.draw, f.fg
        s = self.settings
        sym = s.get("currency", "$") or "$"
        now = datetime.now()
        T = max(1.0, min(1.7, 1 + (min(f.width / 250, f.height / 122) - 1) * 0.5))
        f12 = font(1, round(12 * T))
        f11 = font(1, round(11 * T))
        hdr = round(14 * T)
        footer = f.height - round(14 * T)

        day = Day(self.config.get("schedule", {}))
        fig = self._figures(day, now)

        def spread(items, y, fnt=None):
            fnt = fnt or f12
            widths = [text_width(d, t, fnt) for t in items]
            gap = (f.width - 6 - sum(widths)) / max(1, len(items) - 1)
            x = 3
            for t, w in zip(items, widths):
                d.text((x, y), t, font=fnt, fill=fg)
                x += w + gap

        if not fig:
            d.text((3, 0), "FINANCE", font=f12, fill=fg)
            d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)
            d.text((5, hdr + 18 * T), "No income set.", font=font(1, round(14 * T)), fill=fg)
            d.text((5, hdr + 38 * T), "Add it in Settings, under You.", font=f11, fill=fg)
            d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
            return f.image

        spread([f"Salary: {_money(fig['salary'], sym)}", f"{_money(fig['per_check'], sym)}/check"], 0)
        d.line([(0, hdr), (f.width, hdr)], fill=fg, width=1)

        d.text((3, hdr + 4 * T), "Today so far" if fig["workday"] else "Day off", font=f12, fill=fg)
        big = font(1, round(29 * T))
        amount = _money(fig["earned"], sym, 2 if fig["earned"] < 100 else 0)
        d.text((f.width - 8 - text_width(d, amount, big), hdr + 2 * T), amount, font=big, fill=fg)

        bar_y = hdr + round(38 * T)
        bar_h = round(9 * T)
        bar_w = int(f.width * 0.7)
        bar_x = (f.width - bar_w) // 2
        done = max(0.0, min(1.0, (fig["earned"] / fig["daily"]) if fig["daily"] else 0))
        radius = bar_h / 2
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius, outline=fg, width=1)
        fill = (bar_w - 2) * done
        if fill >= 1:
            d.rounded_rectangle([bar_x + 1, bar_y + 1, bar_x + 1 + fill, bar_y + bar_h - 1],
                                min(radius - 1, fill / 2), fill=fg)
        d.text((bar_x - text_width(d, sym + "0", f11) - 5, bar_y - 1), sym + "0", font=f11, fill=fg)
        d.text((bar_x + bar_w + 5, bar_y - 1), _money(fig["daily"], sym), font=f11, fill=fg)

        row = bar_y + round(19 * T)
        spread([f"{_money(fig['weekly'], sym)}/wk", f"{_money(fig['monthly'], sym)}/mo",
                f"{_money(fig['daily'], sym)}/day"], row)
        if f.height >= 150:
            share = fig["year"] / fig["salary"] if fig["salary"] else 0
            spread([f"{now.year} so far: {_money(fig['year'], sym)}",
                    f"{round(share * 100)}% of the year"], row + round(19 * T), f11)

        d.line([(0, footer), (f.width, footer)], fill=fg, width=1)
        foot = [f"Year {_money(fig['year'], sym)}"]
        activity, _paid, ends = day.current()
        if fig["workday"] and ends is not None and activity:
            left = max(0, ends - day.minute)
            foot.append(f"{int(left // 60)}h {int(left % 60):02d}m left")
            foot.append(f"{_money(fig['daily'] - fig['earned'], sym)} to go")
        elif fig["workday"]:
            foot.append("Not working now")
        spread(foot, footer + 1)
        return f.image
