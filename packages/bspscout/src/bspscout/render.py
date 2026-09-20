"""Readable top-down map renders with world-coordinate grids."""
from __future__ import annotations

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Circle, Rectangle

STOREY = 128.0          # units per floor, for storey banding

_LEVEL_COLORS = [
    "#1f4f8b", "#2e8b57", "#c8a02c", "#d1642a", "#b03a48", "#7d3c98", "#2c7f8f",
]
BELOW_COLOR = "#4a3b6b"
UNREACH_COLOR = "#6b2b2b"


def storey_of(agl: np.ndarray) -> np.ndarray:
    return np.floor((agl + STOREY * 0.5) / STOREY).astype(np.int32)


def _grid_overlay(ax, extent, major=1024, minor=256, color="#ffffff", lw=0.35):
    x0, x1, y0, y1 = extent
    for x in np.arange(np.ceil(x0 / minor) * minor, x1, minor):
        ax.axvline(x, color=color, lw=lw * 0.5, alpha=0.10, zorder=5)
    for y in np.arange(np.ceil(y0 / minor) * minor, y1, minor):
        ax.axhline(y, color=color, lw=lw * 0.5, alpha=0.10, zorder=5)
    xs = np.arange(np.ceil(x0 / major) * major, x1, major)
    ys = np.arange(np.ceil(y0 / major) * major, y1, major)
    for x in xs:
        ax.axvline(x, color=color, lw=lw, alpha=0.28, zorder=6)
    for y in ys:
        ax.axhline(y, color=color, lw=lw, alpha=0.28, zorder=6)
    ax.set_xticks(xs); ax.set_yticks(ys)
    ax.tick_params(labelsize=6, colors="#cccccc", length=2)
    for s in ax.spines.values():
        s.set_color("#555555")


def _scalebar(ax, extent, units=1024):
    x0, x1, y0, y1 = extent
    x = x0 + (x1 - x0) * 0.03
    y = y0 + (y1 - y0) * 0.03
    ax.plot([x, x + units], [y, y], color="w", lw=2.5, zorder=20)
    ax.text(x + units / 2, y + (y1 - y0) * 0.006, f"{units} u  (~{units/16:.0f} m)",
            color="w", ha="center", fontsize=6, zorder=20)


def _north(ax, extent):
    x0, x1, y0, y1 = extent
    x = x1 - (x1 - x0) * 0.045
    y = y1 - (y1 - y0) * 0.06
    ax.annotate("", xy=(x, y + (y1 - y0) * 0.03), xytext=(x, y),
                arrowprops=dict(arrowstyle="-|>", color="w", lw=1.2), zorder=20)
    ax.text(x, y - (y1 - y0) * 0.012, "+Y", color="w", ha="center", fontsize=7, zorder=20)


def _fig(grid, crop, title, sub=""):
    x0, x1, y0, y1 = crop
    aspect = (y1 - y0) / (x1 - x0)
    w = 13.0
    fig, ax = plt.subplots(figsize=(w, w * aspect + 0.9), dpi=190)
    fig.patch.set_facecolor("#0d0d10")
    ax.set_facecolor("#0d0d10")
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_title(title, color="w", fontsize=12, pad=22)
    if sub:
        ax.text(0.5, 1.004, sub, transform=ax.transAxes, ha="center",
                va="bottom", color="#9aa", fontsize=7.5)
    return fig, ax


def _cells_to_image(grid, cells, values, shape, fill=np.nan):
    img = np.full(shape[0] * shape[1], fill, np.float32)
    img[cells] = values
    return img.reshape(shape)


def massing_image(top_z, ground_z):
    """Grey building-mass backdrop from the drawn roof heights."""
    agl = top_z - ground_z
    m = np.isfinite(agl)
    shade = np.zeros_like(agl)
    shade[m] = np.clip(agl[m] / 900.0, 0, 1) ** 0.55
    return np.where(m, 0.13 + shade * 0.35, np.nan)


def render_overview(grid, top_z, surf, agl, reach, ground_z, crop, outpath,
                    markers=None, title="Map overview"):
    shape = (grid.h, grid.w)
    ext = grid.extent
    fig, ax = _fig(grid, crop, title,
                   f"walkable area coloured by storey above ground (world z={ground_z:.0f})")
    mass = massing_image(top_z, ground_z)
    ax.imshow(mass, origin="lower", extent=ext, cmap="Greys_r", vmin=0, vmax=1,
              interpolation="nearest", zorder=1)

    st = storey_of(agl)
    order = np.argsort(agl)
    cells = surf["cell"][order]; sts = st[order]; rc = reach[order]
    rgb = np.zeros((shape[0] * shape[1], 4), np.float32)
    for lvl in np.unique(sts):
        col = BELOW_COLOR if lvl < 0 else _LEVEL_COLORS[min(int(lvl), len(_LEVEL_COLORS) - 1)]
        c = matplotlib.colors.to_rgba(col)
        sel = sts == lvl
        if (sel & rc).any():
            rgb[cells[sel & rc]] = (c[0], c[1], c[2], 0.95)
    u = matplotlib.colors.to_rgba(UNREACH_COLOR)
    if (~rc).any():
        rgb[cells[~rc]] = (u[0], u[1], u[2], 0.55)
    ax.imshow(rgb.reshape(shape[0], shape[1], 4), origin="lower", extent=ext,
              interpolation="nearest", zorder=3)

    _grid_overlay(ax, crop)
    _scalebar(ax, crop); _north(ax, crop)
    if markers:
        draw_markers(ax, markers)
    handles = [plt.Line2D([], [], marker="s", ls="", color=BELOW_COLOR, label="below ground")]
    for i, c in enumerate(_LEVEL_COLORS[:5]):
        handles.append(plt.Line2D([], [], marker="s", ls="", color=c,
                                  label=f"storey +{i}" if i else "ground"))
    handles.append(plt.Line2D([], [], marker="s", ls="", color=UNREACH_COLOR,
                              label="sealed off (no route in)"))
    leg = ax.legend(handles=handles, loc="lower right", fontsize=6, framealpha=0.75,
                    facecolor="#16161c", edgecolor="#444", labelcolor="#ddd")
    leg.set_zorder(21)
    fig.savefig(outpath, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def render_height(grid, surf, agl, reach, crop, outpath, ground_z):
    shape = (grid.h, grid.w)
    img = _cells_to_image(grid, surf["cell"][reach], agl[reach], shape)
    fig, ax = _fig(grid, crop, "Walkable height above ground",
                   f"colour = units above the global ground plane (world z={ground_z:.0f})")
    lo = float(np.nanpercentile(img, 1)); hi = float(np.nanpercentile(img, 99.5))
    cmap = LinearSegmentedColormap.from_list(
        "agl", ["#101830", "#1f4f8b", "#2e8b57", "#c8c03c", "#d1642a", "#f2e6d8"])
    im = ax.imshow(img, origin="lower", extent=grid.extent, cmap=cmap,
                   norm=Normalize(min(lo, 0), max(hi, 128)), interpolation="nearest", zorder=2)
    cb = fig.colorbar(im, ax=ax, fraction=0.022, pad=0.01)
    cb.set_label("units above ground", color="#ccc", fontsize=7)
    cb.ax.tick_params(labelsize=6, colors="#ccc")
    _grid_overlay(ax, crop); _scalebar(ax, crop); _north(ax, crop)
    fig.savefig(outpath, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def render_slice(grid, top_z, surf, agl, reach, crop, outpath, band, ground_z, markers=None):
    """A single storey band drawn as a floor plan."""
    lo, hi = band
    sel = reach & (agl >= lo) & (agl < hi)
    shape = (grid.h, grid.w)
    fig, ax = _fig(grid, crop, f"Floor plan  {lo:+.0f} .. {hi:+.0f} units above ground",
                   "solid = standable at this level; grey = building mass overhead")
    ax.imshow(massing_image(top_z, ground_z), origin="lower", extent=grid.extent,
              cmap="Greys_r", vmin=0, vmax=1.6, interpolation="nearest", zorder=1)
    img = _cells_to_image(grid, surf["cell"][sel], np.ones(int(sel.sum()), np.float32), shape, 0.0)
    rgba = np.zeros((shape[0], shape[1], 4), np.float32)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 0.30, 0.85, 0.55
    rgba[..., 3] = img * 0.95
    ax.imshow(rgba, origin="lower", extent=grid.extent, interpolation="nearest", zorder=3)
    _grid_overlay(ax, crop); _scalebar(ax, crop); _north(ax, crop)
    if markers:
        draw_markers(ax, markers)
    fig.savefig(outpath, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def render_scatter(grid, top_z, ground_z, crop, xy, values, outpath, title, sub,
                   cmap="inferno", vmin=0.0, vmax=None, label="", markers=None,
                   size=9.0):
    """Sampled point values over the building-mass backdrop.

    A scatter rather than an image because the ray measures are sampled at a few
    thousand positions, not rasterised over every cell - drawing them as a filled
    grid would imply a resolution that is not there.
    """
    fig, ax = _fig(grid, crop, title, sub)
    ax.imshow(massing_image(top_z, ground_z), origin="lower", extent=grid.extent,
              cmap="Greys_r", vmin=0, vmax=1.6, interpolation="nearest", zorder=1)
    sc = ax.scatter(xy[:, 0], xy[:, 1], c=values, s=size, cmap=cmap, vmin=vmin,
                    vmax=vmax, linewidths=0, zorder=3)
    cb = fig.colorbar(sc, ax=ax, fraction=0.025, pad=0.01)
    cb.set_label(label, color="#ddd", fontsize=7)
    cb.ax.tick_params(colors="#ddd", labelsize=6)
    cb.outline.set_edgecolor("#444")
    _grid_overlay(ax, crop); _scalebar(ax, crop); _north(ax, crop)
    if markers:
        draw_markers(ax, markers)
    fig.savefig(outpath, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def draw_markers(ax, markers):
    for m in markers:
        x, y = m["xy"]
        ax.plot([x], [y], marker=m.get("marker", "o"), ms=m.get("ms", 6),
                mfc=m.get("color", "#fff"), mec="k", mew=0.6, zorder=15)
        if m.get("label"):
            ax.annotate(m["label"], (x, y), textcoords="offset points",
                        xytext=(7, 5), fontsize=m.get("fs", 7), color=m.get("color", "#fff"),
                        zorder=16,
                        path_effects=[matplotlib.patheffects.withStroke(linewidth=2, foreground="#000")])
        if m.get("radius"):
            ax.add_patch(Circle((x, y), m["radius"], fill=False,
                                ec=m.get("color", "#fff"), lw=1.1, ls="--",
                                alpha=0.9, zorder=14))
