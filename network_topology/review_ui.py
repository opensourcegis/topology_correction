# SPDX-License-Identifier: GPL-2.0-or-later

"""Step through each dangling end. The caller pans the open map to that error.

Enter accepts the correction. Space rejects it and leaves the end unchanged.
Escape cancels the whole review and writes nothing.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any, Callable, Sequence

from network_topology.dangle_resolver import Correction, resolve_dangles
from network_topology.geometry import Point

# 5x7 glyphs, each row is five bits with the high bit on the left.
_FONT = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "0": (14, 17, 19, 21, 25, 17, 14),
    "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 6, 8, 16, 31),
    "3": (14, 17, 1, 6, 1, 17, 14),
    "4": (2, 6, 10, 18, 31, 2, 2),
    "5": (31, 16, 30, 1, 1, 17, 14),
    "6": (6, 8, 16, 30, 17, 17, 14),
    "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 2, 12),
    ".": (0, 0, 0, 0, 0, 12, 12),
    ",": (0, 0, 0, 0, 0, 4, 8),
    "/": (1, 2, 4, 8, 16, 0, 0),
    "-": (0, 0, 0, 31, 0, 0, 0),
    "A": (14, 17, 17, 31, 17, 17, 17),
    "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14),
    "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 14),
    "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 2, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 25, 21, 19, 17, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (14, 17, 16, 14, 1, 17, 14),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
    "Z": (31, 1, 2, 4, 8, 16, 31),
    "a": (0, 0, 14, 1, 15, 17, 15),
    "b": (16, 16, 30, 17, 17, 17, 30),
    "c": (0, 0, 14, 16, 16, 17, 14),
    "d": (1, 1, 15, 17, 17, 17, 15),
    "e": (0, 0, 14, 17, 31, 16, 14),
    "f": (6, 8, 8, 30, 8, 8, 8),
    "g": (0, 0, 15, 17, 15, 1, 14),
    "h": (16, 16, 30, 17, 17, 17, 17),
    "i": (4, 0, 12, 4, 4, 4, 14),
    "j": (2, 0, 6, 2, 2, 18, 12),
    "k": (16, 16, 18, 20, 24, 20, 18),
    "l": (12, 4, 4, 4, 4, 4, 14),
    "m": (0, 0, 26, 21, 21, 21, 21),
    "n": (0, 0, 30, 17, 17, 17, 17),
    "o": (0, 0, 14, 17, 17, 17, 14),
    "p": (0, 0, 30, 17, 30, 16, 16),
    "q": (0, 0, 15, 17, 15, 1, 1),
    "r": (0, 0, 22, 24, 16, 16, 16),
    "s": (0, 0, 15, 16, 14, 1, 30),
    "t": (8, 8, 30, 8, 8, 9, 6),
    "u": (0, 0, 17, 17, 17, 17, 15),
    "v": (0, 0, 17, 17, 17, 10, 4),
    "w": (0, 0, 17, 17, 21, 21, 10),
    "x": (0, 0, 17, 10, 4, 10, 17),
    "y": (0, 0, 17, 17, 15, 1, 14),
    "z": (0, 0, 31, 2, 4, 8, 31),
}

_GREEN = (27, 138, 62)
_RED = (209, 36, 47)
_INK = (32, 40, 48)
_MUTED = (90, 102, 112)
_PAPER = (244, 246, 245)
_LINE = (55, 68, 80)
_CONTEXT = (176, 184, 190)
_TAIL = (214, 96, 96)


def dedupe_corrections(corrections: Sequence[Correction]) -> list[Correction]:
    """One prompt per dangling end. The same end must not be asked twice."""
    unique: list[Correction] = []
    seen: set[tuple[int, int, bool, str]] = set()
    for correction in corrections:
        key = (
            correction.feature_index,
            correction.part_in_feature,
            correction.at_start,
            correction.kind,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(correction)
    return unique


def highlight_radius(correction: Correction) -> float:
    """Half-width of the highlight, in layer units, so the end is obvious when zoomed."""
    anchor = correction.before[0] if correction.at_start else correction.before[-1]
    proposed = correction.after[0] if correction.at_start else correction.after[-1]
    gap = ((proposed.x - anchor.x) ** 2 + (proposed.y - anchor.y) ** 2) ** 0.5
    return max(gap * 0.45, 1e-8)


def highlight_band(points: Sequence[Point], radius: float) -> list[Point]:
    """A box around a segment, wide enough to see at the review zoom."""
    start, end = points[0], points[1]
    dx = end.x - start.x
    dy = end.y - start.y
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0.0:
        dx, dy, length = 1.0, 0.0, 1.0
    px = -dy / length * radius
    py = dx / length * radius
    return [
        Point(start.x + px, start.y + py),
        Point(end.x + px, end.y + py),
        Point(end.x - px, end.y - py),
        Point(start.x - px, start.y - py),
    ]


def review_highlight(correction: Correction) -> dict[str, Any]:
    """Pieces of one dangling end to draw on the map.

    ``dangle`` is the end segment under review. ``change`` is the extension
    that would be added, or the tail that would be cut. ``anchor`` is the
    free vertex.
    """
    before = correction.before
    after = correction.after
    if correction.at_start:
        anchor = before[0]
        neighbor = before[1] if len(before) > 1 else before[0]
        proposed = after[0]
    else:
        anchor = before[-1]
        neighbor = before[-2] if len(before) > 1 else before[-1]
        proposed = after[-1]
    return {
        "dangle": [neighbor, anchor],
        "change": [anchor, proposed],
        "anchor": anchor,
        "kind": correction.kind,
    }


def review_zoom_extent(correction: Correction) -> tuple[float, float, float, float]:
    """Layer extent centred on the dangling end and the correction.

    The window is a few times the gap. Whole lines that merely pass near the
    error are left outside the frame so the map does not zoom out to them.
    """
    anchor = correction.before[0] if correction.at_start else correction.before[-1]
    proposed = correction.after[0] if correction.at_start else correction.after[-1]
    center_x = (anchor.x + proposed.x) / 2.0
    center_y = (anchor.y + proposed.y) / 2.0
    gap_xy = ((proposed.x - anchor.x) ** 2 + (proposed.y - anchor.y) ** 2) ** 0.5
    # The gap occupies about a quarter of the view, with the join in the middle.
    half = gap_xy * 2.0
    if half <= 0.0:
        half = max(abs(center_x), abs(center_y), 1.0) * 1e-8
    return center_x - half, center_y - half, center_x + half, center_y + half


def decision_for_key(keysym: str) -> bool | None:
    """Enter accepts. Space rejects. Any other key is ignored."""
    if keysym in ("Return", "KP_Enter"):
        return True
    if keysym == "space":
        return False
    return None


def claim_choice(state: dict) -> bool:
    """Accept one answer for the end currently on screen.

    A second key or click for that same end returns False and must not advance.
    """
    if state.get("busy"):
        return False
    state["busy"] = True
    return True


def release_choice(state: dict) -> None:
    """Allow the next dangling end to be answered."""
    state["busy"] = False


def is_review_mode(mode: str | None) -> bool:
    return str(mode or "").strip().lower() == "review"


def collect_corrections(features, tolerance: float, **kwargs) -> list[Correction]:
    """Corrections the automatic pass would make, without keeping that geometry."""
    found: list[Correction] = []

    def decide(correction: Correction) -> bool:
        found.append(correction)
        return True

    resolve_dangles(features, tolerance, decide=decide, **kwargs)
    found.sort(
        key=lambda correction: (
            correction.part_index,
            correction.kind != "overshoot",
            not correction.at_start,
        )
    )
    return dedupe_corrections(found)


def apply_decisions(features, tolerance: float, corrections: Sequence[Correction], accepted: Sequence[bool], **kwargs):
    """Apply the corrections whose matching flag is true."""
    chosen = {correction.key for correction, yes in zip(corrections, accepted) if yes}

    def decide(correction: Correction) -> bool:
        return correction.key in chosen

    return resolve_dangles(features, tolerance, decide=decide, **kwargs)


def _put(pixels: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        index = (y * width + x) * 3
        pixels[index : index + 3] = bytes(color)


def _fill_rect(pixels, width, height, x0, y0, x1, y1, color) -> None:
    for y in range(max(0, y0), min(height, y1)):
        row = y * width * 3
        span = bytes(color) * (min(width, x1) - max(0, x0))
        start = row + max(0, x0) * 3
        pixels[start : start + len(span)] = span


def _line(pixels, width, height, x0, y0, x1, y1, color, thickness: int = 1) -> None:
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    steps = max(dx, dy, 1)
    radius = thickness // 2
    for step in range(steps + 1):
        x = int(round(x0 + (x1 - x0) * step / steps))
        y = int(round(y0 + (y1 - y0) * step / steps))
        for oy in range(-radius, radius + 1):
            for ox in range(-radius, radius + 1):
                if ox * ox + oy * oy <= radius * radius + radius:
                    _put(pixels, width, height, x + ox, y + oy, color)


def _circle(pixels, width, height, cx, cy, radius, color) -> None:
    r2 = radius * radius
    for y in range(cy - radius, cy + radius + 1):
        for x in range(cx - radius, cx + radius + 1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                _put(pixels, width, height, x, y, color)


def _text(pixels, width, height, x, y, text: str, color, scale: int = 2) -> None:
    cursor = x
    for char in text:
        glyph = _FONT.get(char, _FONT.get(char.upper(), _FONT[" "]))
        for row, bits in enumerate(glyph):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    _fill_rect(
                        pixels,
                        width,
                        height,
                        cursor + col * scale,
                        y + row * scale,
                        cursor + (col + 1) * scale,
                        y + (row + 1) * scale,
                        color,
                    )
        cursor += 6 * scale


def _project(points: Sequence[Point], bounds, left, top, right, bottom):
    minx, miny, maxx, maxy = bounds
    span = max(maxx - minx, maxy - miny, 1e-12)
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    draw_w = right - left
    draw_h = bottom - top
    scale = min(draw_w, draw_h) / span

    def xy(point: Point) -> tuple[int, int]:
        x = left + draw_w / 2.0 + (point.x - cx) * scale
        y = top + draw_h / 2.0 - (point.y - cy) * scale
        return int(round(x)), int(round(y))

    return xy


def _bounds(correction: Correction):
    points = list(correction.before) + list(correction.after)
    for part in correction.context:
        points.extend(part)
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    pad = max(maxx - minx, maxy - miny, 1e-9) * 0.25
    return minx - pad, miny - pad, maxx + pad, maxy + pad


def _draw_chain(pixels, width, height, chain, xy, color, thickness) -> None:
    screen = [xy(point) for point in chain]
    for start, end in zip(screen, screen[1:]):
        _line(pixels, width, height, start[0], start[1], end[0], end[1], color, thickness)


def render_review_png(
    correction: Correction,
    index: int = 1,
    total: int = 1,
    unit: str = "",
    width: int = 960,
    height: int = 640,
) -> bytes:
    """Picture of one error with a green tick and a red cross."""
    pixels = bytearray(_PAPER * width * height)
    _fill_rect(pixels, width, height, 0, 0, width, 64, (255, 255, 255))
    kind = "Undershoot" if correction.kind == "undershoot" else "Overshoot"
    end = "start" if correction.at_start else "end"
    unit_text = f" {unit}" if unit else ""
    _text(pixels, width, height, 28, 14, f"{kind}   {index} / {total}", _INK, 3)
    _text(
        pixels,
        width,
        height,
        28,
        42,
        f"Feature {correction.feature_index + 1}, {end}.  Gap {correction.gap:.2f}{unit_text}",
        _MUTED,
        2,
    )

    map_top, map_bottom = 80, 500
    _fill_rect(pixels, width, height, 24, map_top, width - 24, map_bottom, (255, 255, 255))
    bounds = _bounds(correction)
    xy = _project(correction.before, bounds, 48, map_top + 16, width - 48, map_bottom - 16)
    for part in correction.context:
        _draw_chain(pixels, width, height, part, xy, _CONTEXT, 3)
    if correction.kind == "overshoot":
        _draw_chain(pixels, width, height, correction.before, xy, _TAIL, 5)
        _draw_chain(pixels, width, height, correction.after, xy, _LINE, 5)
    else:
        _draw_chain(pixels, width, height, correction.before, xy, _LINE, 5)
        _draw_chain(pixels, width, height, correction.after, xy, _GREEN, 5)
    anchor = correction.before[0] if correction.at_start else correction.before[-1]
    ax, ay = xy(anchor)
    _circle(pixels, width, height, ax, ay, 7, (232, 122, 26))

    _button(pixels, width, height, 36, 524, 450, 612, _GREEN, "tick", "Accept")
    _button(pixels, width, height, 510, 524, 924, 612, _RED, "cross", "Reject")
    return _png(width, height, pixels)


def _button(pixels, width, height, x0, y0, x1, y1, color, mark: str, label: str) -> None:
    _fill_rect(pixels, width, height, x0, y0, x1, y1, color)
    cx = x0 + 54
    cy = (y0 + y1) // 2
    if mark == "tick":
        _line(pixels, width, height, cx - 16, cy, cx - 4, cy + 14, (255, 255, 255), 5)
        _line(pixels, width, height, cx - 4, cy + 14, cx + 20, cy - 16, (255, 255, 255), 5)
    else:
        _line(pixels, width, height, cx - 14, cy - 14, cx + 14, cy + 14, (255, 255, 255), 5)
        _line(pixels, width, height, cx + 14, cy - 14, cx - 14, cy + 14, (255, 255, 255), 5)
    _text(pixels, width, height, x0 + 96, cy - 8, label, (255, 255, 255), 3)


def _png(width: int, height: int, pixels: bytearray) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3]) for y in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _schematic_ppm(correction: Correction) -> bytes:
    """Small map the review window can show without Pillow."""
    width, height = 720, 420
    pixels = bytearray((255, 255, 255) * width * height)
    bounds = _bounds(correction)
    xy = _project(correction.before, bounds, 24, 24, width - 24, height - 24)
    for part in correction.context:
        _draw_chain(pixels, width, height, part, xy, _CONTEXT, 3)
    if correction.kind == "overshoot":
        _draw_chain(pixels, width, height, correction.before, xy, _TAIL, 4)
        _draw_chain(pixels, width, height, correction.after, xy, _LINE, 4)
    else:
        _draw_chain(pixels, width, height, correction.before, xy, _LINE, 4)
        _draw_chain(pixels, width, height, correction.after, xy, _GREEN, 4)
    anchor = correction.before[0] if correction.at_start else correction.before[-1]
    ax, ay = xy(anchor)
    _circle(pixels, width, height, ax, ay, 6, (232, 122, 26))
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    return header + bytes(pixels)


def review_corrections(
    corrections: Sequence[Correction],
    on_show: Callable[[Correction], str | None] | None = None,
    unit: str = "",
) -> list[bool] | None:
    """Visit each error. True accepts it. None means the review was cancelled."""
    if not corrections:
        return []
    import os
    import tempfile
    import tkinter as tk

    answers: list[bool] = []
    cancelled = {"value": False}
    position = {"i": 0}
    # When the caller moves the map, keep this bar out of the way.
    map_review = on_show is not None

    root = tk.Tk()
    root.title("Review dangles")
    root.configure(bg="#f4f6f5")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    title = tk.Label(root, bg="#f4f6f5", fg="#202830", font=("Segoe UI", 16, "bold"))
    title.pack(anchor="w", padx=16, pady=(12, 0))
    detail = tk.Label(root, bg="#f4f6f5", fg="#5a6670", font=("Segoe UI", 11))
    detail.pack(anchor="w", padx=16, pady=(2, 4))
    hint = tk.Label(
        root,
        bg="#f4f6f5",
        fg="#202830",
        font=("Segoe UI", 11, "bold"),
        text="Enter accept     Space reject     Esc cancel",
    )
    hint.pack(anchor="w", padx=16, pady=(0, 8))

    image_label = tk.Label(root, bg="white", bd=0)
    if not map_review:
        image_label.pack(padx=16, pady=(0, 12))
    holder: dict[str, tk.PhotoImage] = {}

    buttons = tk.Frame(root, bg="#f4f6f5")
    buttons.pack(fill="x", padx=16, pady=(0, 16))

    def close_cancelled():
        cancelled["value"] = True
        root.quit()

    root.protocol("WM_DELETE_WINDOW", close_cancelled)

    def choose(accept: bool):
        # Enter and Space can be delivered twice (key plus a focused control).
        # The second delivery must not move on to the next dangling end.
        if not claim_choice(position):
            return
        answers.append(accept)
        position["i"] += 1
        if position["i"] >= len(corrections):
            root.quit()
            return
        show()
        root.after(300, lambda: release_choice(position))

    def on_key(event):
        if event.keysym == "Escape":
            close_cancelled()
            return "break"
        decision = decision_for_key(event.keysym)
        if decision is None:
            return None
        choose(decision)
        return "break"

    def choice_label(text, color, accept):
        label = tk.Label(
            buttons,
            text=text,
            bg=color,
            fg="white",
            font=("Segoe UI", 14, "bold"),
            padx=18,
            pady=10,
            cursor="hand2",
        )
        label.pack(side="left", expand=True, fill="x", padx=(0 if accept else 8, 8 if accept else 0))
        label.bind("<Button-1>", lambda _event: choose(accept))
        return label

    choice_label("Accept   Enter", "#1b8a3e", True)
    choice_label("Reject   Space", "#d1242f", False)
    root.bind("<KeyPress>", on_key)

    def place_bar():
        root.update_idletasks()
        width = max(root.winfo_reqwidth(), 680)
        height = root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        if map_review:
            y = max(0, root.winfo_screenheight() - height - 64)
        else:
            y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")

    def show():
        correction = corrections[position["i"]]
        kind = "Undershoot" if correction.kind == "undershoot" else "Overshoot"
        end = "start" if correction.at_start else "end"
        unit_text = f" {unit}" if unit else ""
        title.configure(text=f"{kind}    {position['i'] + 1} / {len(corrections)}")
        detail.configure(
            text=(
                f"Feature {correction.feature_index + 1}, {end}.  "
                f"Gap {correction.gap:.2f}{unit_text}"
            )
        )
        root.withdraw()
        root.update_idletasks()
        if on_show:
            on_show(correction)
        else:
            handle = tempfile.NamedTemporaryFile(suffix=".ppm", delete=False)
            handle.write(_schematic_ppm(correction))
            handle.close()
            try:
                photo = tk.PhotoImage(file=handle.name, master=root)
            finally:
                os.unlink(handle.name)
            holder["photo"] = photo
            image_label.configure(image=photo)
        root.deiconify()
        place_bar()
        root.lift()
        root.focus_force()

    show()
    root.mainloop()
    root.destroy()
    if cancelled["value"]:
        return None
    return answers
