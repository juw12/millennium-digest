"""Small inline-SVG charts for the dashboard (no external libraries, crisp at any zoom)."""
import html
import math

INK = "#0f172a"
MUTED = "#64748b"
GRID = "#e2e8f0"
FONT = "font-family:'Segoe UI Variable','Segoe UI',system-ui,sans-serif"


def _nice_ticks(low, high, count=5):
    span = high - low
    if span <= 0:
        return [low]
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = min((m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw), default=raw)
    start = math.ceil(low / step) * step
    ticks = []
    value = start
    while value <= high + 1e-9:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _fmt(value):
    if abs(value) >= 10 or float(value).is_integer():
        return f"{value:.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def hbar_chart(items, color="#4f46e5", targets=None, **_ignored):
    """Horizontal bars as HTML (text stays readable at every screen size). items: [(label, value)].
    targets: {label: filter value}; bars with a target are clickable (they filter the paper list)."""
    targets = targets or {}
    items = sorted(items, key=lambda item: -item[1])
    peak = max([value for _, value in items] + [1])
    rows = []
    for label, value in items:
        width = 100 * value / peak if value else 0
        target = targets.get(label) if value else None
        attrs = (
            f' data-filter-target="{html.escape(target)}" role="button" tabindex="0" title="Show these papers"'
            if target else ""
        )
        rows.append(
            f'<div class="hb-row"{attrs}><span class="hb-label">{html.escape(label)}</span>'
            f'<span class="hb-track"><span class="hb-bar" style="width:{width:.1f}%;background:{color}"></span></span>'
            f'<span class="hb-value{" zero" if not value else ""}">{value}</span></div>'
        )
    return '<div class="hbars" role="img" aria-label="New papers per topic">' + "".join(rows) + "</div>"


def stacked_bar(segments, **_ignored):
    """One bar split into segments, with an HTML legend. segments: [(label, value, color)]."""
    total = sum(value for _, value, _ in segments) or 1
    parts = ['<div class="sb" role="img" aria-label="Outreach pipeline">']
    for label, value, color in segments:
        if value:
            parts.append(
                f'<span class="sb-seg" style="flex:{value};background:{color}" title="{html.escape(label)}">{value}</span>'
            )
    parts.append('</div><div class="legend">')
    for label, value, color in segments:
        parts.append(
            f'<span class="lg"><span class="lg-dot" style="background:{color};border-radius:3px"></span>'
            f"{html.escape(label)} ({value})</span>"
        )
    parts.append("</div>")
    return "".join(parts)


def line_chart(series, x_range, y_range=None, width=560, height=300, x_label="", y_label="",
               vlines=(), zero_line=True, aria="chart"):
    """series: [{"name", "xs", "ys", "color", "style": "line"|"dash"|"dots"}]."""
    left, right, top, bottom = 52, 14, 14, 44
    plot_w = width - left - right
    plot_h = height - top - bottom
    x0, x1 = x_range
    if y_range is None:
        values = [y for s in series for y in s["ys"] if y is not None and math.isfinite(y)]
        pad = (max(values) - min(values)) * 0.08 or 1
        y_range = (min(values) - pad, max(values) + pad)
    y0, y1 = y_range

    def px(x):
        return left + (x - x0) / (x1 - x0) * plot_w

    def py(y):
        return top + (y1 - y) / (y1 - y0) * plot_h

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{html.escape(aria)}" style="{FONT}">']
    for tick in _nice_ticks(y0, y1):
        parts.append(
            f'<line x1="{left}" x2="{left + plot_w}" y1="{py(tick):.1f}" y2="{py(tick):.1f}" stroke="{GRID}"/>'
            f'<text x="{left - 8}" y="{py(tick):.1f}" font-size="11" fill="{MUTED}" text-anchor="end" '
            f'dominant-baseline="middle">{_fmt(tick)}</text>'
        )
    for tick in _nice_ticks(x0, x1):
        parts.append(
            f'<text x="{px(tick):.1f}" y="{top + plot_h + 18}" font-size="11" fill="{MUTED}" '
            f'text-anchor="middle">{_fmt(tick)}</text>'
        )
    if zero_line and y0 < 0 < y1:
        parts.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{py(0):.1f}" y2="{py(0):.1f}" stroke="#94a3b8"/>')
    for x_value, label in vlines:
        parts.append(
            f'<line x1="{px(x_value):.1f}" x2="{px(x_value):.1f}" y1="{top}" y2="{top + plot_h}" '
            f'stroke="#94a3b8" stroke-dasharray="3 4"/>'
            f'<text x="{px(x_value) + 5:.1f}" y="{top + 12}" font-size="11" fill="{MUTED}">{html.escape(label)}</text>'
        )
    for s in series:
        data = [(x, y) for x, y in zip(s["xs"], s["ys"]) if y is not None and math.isfinite(y)]
        points = [(px(x), py(y)) for x, y in data]
        name = html.escape(s.get("name", ""))
        if s.get("style") == "dots":
            for (x, y), (xv, yv) in zip(points, data):
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{s["color"]}" stroke="#fff" stroke-width="1.5">'
                    f'<title>{name}: x = {_fmt(xv)}, y = {yv:.3g}</title></circle>'
                )
            continue
        dash = ' stroke-dasharray="6 5"' if s.get("style") == "dash" else ""
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
        parts.append(
            f'<path d="{path}" fill="none" stroke="{s["color"]}" stroke-width="{2.4 if not dash else 2}"{dash} '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        if not dash:
            # invisible hover targets: hovering a point shows its value
            for (x, y), (xv, yv) in zip(points, data):
                parts.append(
                    f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="6"><title>{name}: x = {_fmt(xv)}, '
                    f'y = {yv:.3g}</title></circle>'
                )
    parts.append(
        f'<text x="{left + plot_w / 2}" y="{height - 6}" font-size="12" fill="{MUTED}" text-anchor="middle">{html.escape(x_label)}</text>'
        f'<text x="14" y="{top + plot_h / 2}" font-size="12" fill="{MUTED}" text-anchor="middle" '
        f'transform="rotate(-90 14 {top + plot_h / 2})">{html.escape(y_label)}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def legend(entries):
    """HTML legend under a chart. entries: [(label, color, style)]."""
    items = []
    for label, color, style in entries:
        if style == "dots":
            mark = f'<span class="lg-dot" style="background:{color}"></span>'
        elif style == "dash":
            mark = f'<span class="lg-line dash" style="border-color:{color}"></span>'
        else:
            mark = f'<span class="lg-line" style="border-color:{color}"></span>'
        items.append(f'<span class="lg">{mark}{html.escape(label)}</span>')
    return '<div class="legend">' + "".join(items) + "</div>"
