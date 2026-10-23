"""Our own graphs, drawn with QPainter: a border, a y axis with units, an x
axis with times, and the agent's samples as a line. Where there are no
samples, a hatched band says why instead of a made-up line (house rule 1).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .ui import clock
from .words import text

Point = tuple[float, float | None]  # (time, value or None when not measured)


def gaps(
    points: list[Point], start: float, end: float, gap: float
) -> list[tuple[float, float, str]]:
    """Where the graph has no line, and why: the server was off (the agent
    sampled, but there was no value), or the agent itself wasn't running
    (no samples at all for longer than gap seconds)."""
    out: list[tuple[float, float, str]] = []
    edges = [(start, None), *points, (end, None)]
    for (t0, v0), (t1, _v1) in zip(edges, edges[1:], strict=False):
        if t1 - t0 > gap:
            out.append((t0, t1, "desktop.graph.agent_gap"))
        elif v0 is None and t0 != start:
            out.append((t0, t1, "desktop.graph.server_off"))
    merged: list[tuple[float, float, str]] = []
    for band in out:
        if merged and merged[-1][2] == band[2] and merged[-1][1] >= band[0]:
            merged[-1] = (merged[-1][0], band[1], band[2])
        else:
            merged.append(band)
    return merged


class Graph(QWidget):
    def __init__(self, title: str, unit: str, top: float) -> None:
        super().__init__()
        self.title, self.unit, self.top = title, unit, top
        self.points: list[Point] = []
        self.start = self.end = 0.0
        self.gap = 60.0
        self.guide: tuple[float, str] | None = None  # a dashed line: (value, its words)
        self.colors = {"line": "#0066CC", "text": "#5F5F65", "border": "#DCDCE0", "plot": "#FFFFFF"}
        self.setMinimumSize(160, 110)

    def show_points(self, points: list[Point], start: float, end: float, gap: float) -> None:
        self.points, self.start, self.end, self.gap = points, start, end, gap
        known = [v for _t, v in points if v is not None]
        highest = max(known + [self.guide[0] if self.guide else 0.0], default=0.0)
        if highest > self.top:
            self.top = highest * 1.15
        if known:
            self.setAccessibleDescription(
                text(
                    "desktop.graph.text",
                    name=self.title,
                    now=self.value(known[-1]),
                    low=self.value(min(known)),
                    high=self.value(max(known)),
                )
            )
        else:
            self.setAccessibleDescription(text("overview.not_measured"))
        self.setAccessibleName(self.title)
        self.update()

    def value(self, v: float) -> str:
        return f"{v:.1f} {self.unit}".strip() if v < 100 else f"{v:.0f} {self.unit}".strip()

    def axis_labels(self) -> list[str]:
        return [f"{self.top * frac:g} {self.unit}".strip() for frac in (0, 0.5, 1)]

    def paintEvent(self, event) -> None:
        c = self.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = p.font()
        font.setPointSizeF(font.pointSizeF() * 0.8)
        p.setFont(font)
        left, bottom = 44, 20
        plot = QRectF(left, 6, self.width() - left - 6, self.height() - bottom - 6)
        p.setPen(QPen(QColor(c["border"]), 1))
        p.setBrush(QColor(c["plot"]))
        p.drawRoundedRect(plot, 4, 4)
        muted = QColor(c["text"])
        for frac, words in zip((0, 0.5, 1), self.axis_labels(), strict=True):
            y = plot.bottom() - frac * plot.height()
            if 0 < frac < 1:
                p.setPen(QPen(QColor(c["border"]), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(
                QRectF(0, y - 8, left - 5, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                words,
            )
        span = max(self.end - self.start, 1.0)

        def x_of(t: float) -> float:
            return plot.left() + (t - self.start) / span * plot.width()

        for i, frac in enumerate((0, 0.5, 1)):
            x = plot.left() + frac * plot.width()
            align = [
                Qt.AlignmentFlag.AlignLeft,
                Qt.AlignmentFlag.AlignHCenter,
                Qt.AlignmentFlag.AlignRight,
            ][i]
            p.drawText(
                QRectF(x - [0, 30, 60][i], plot.bottom() + 3, 60, 14),
                align | Qt.AlignmentFlag.AlignTop,
                clock(self.start + frac * span) if self.end else "",
            )
        p.setClipRect(plot)
        p.setBrush(Qt.BrushStyle.NoBrush)
        line = QColor(c["line"])
        path = QPainterPath()
        last: float | None = None  # time of the sample the line comes from
        for t, v in self.points:
            if v is None:
                last = None
                continue
            point = QPointF(x_of(t), plot.bottom() - min(v / self.top, 1) * plot.height())
            if last is not None and t - last <= self.gap:
                path.lineTo(point)
            else:
                path.moveTo(point)
            last = t
        p.setPen(QPen(line, 2))
        p.drawPath(path)
        if self.guide:
            y = plot.bottom() - min(self.guide[0] / self.top, 1) * plot.height()
            p.setPen(QPen(line, 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(muted)
            p.drawText(
                QRectF(plot.left(), y - 16, plot.width() - 4, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                self.guide[1],
            )
        p.setClipping(False)
        for t0, t1, reason in gaps(self.points, self.start, self.end, self.gap):
            x0, x1 = max(x_of(t0), plot.left()), min(x_of(t1), plot.right())
            band = QColor(c["text"])
            band.setAlpha(60)
            p.fillRect(
                QRectF(x0, plot.top() + 1, x1 - x0, plot.height() - 2),
                QBrush(band, Qt.BrushStyle.BDiagPattern),
            )
            words = text(reason)
            wide = p.fontMetrics().horizontalAdvance(words) + 10
            if x1 - x0 > wide:
                tag = QRectF(
                    (x0 + x1 - wide) / 2, plot.top() + 4, wide, p.fontMetrics().height() + 2
                )
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(c["plot"]))
                p.drawRoundedRect(tag, 4, 4)
                p.setPen(muted)
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, words)
