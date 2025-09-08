# Minimal centerline compute: per-letter + total length
# NOTE: This uses a raster skeleton approach (fast & robust for signage).

from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from skimage.morphology import skeletonize
import numpy as np
import tempfile, os

class GlyphToPolygonPen(BasePen):
    def __init__(self, glyphSet):
        super().__init__(glyphSet)
        self.points = []
        self.cur = []

    def _moveTo(self, p):
        if self.cur: self.points.append(self.cur); self.cur = []
        self.cur.append(p)

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        steps = 24
        x0, y0 = self.cur[-1]
        for t in np.linspace(0, 1, steps, endpoint=True):
            x = (1-t)**3 * x0 + 3*(1-t)**2*t * p1[0] + 3*(1-t)*t**2 * p2[0] + t**3 * p3[0]
            y = (1-t)**3 * y0 + 3*(1-t)**2*t * p1[1] + 3*(1-t)*t**2 * p2[1] + t**3 * p3[1]
            self.cur.append((x, y))

    def _closePath(self):
        if self.cur:
            self.points.append(self.cur)
            self.cur = []

    def get_polygons(self):
        return [Polygon(c) for c in self.points if len(c) > 2]

def _glyph_polygon(font, ch):
    glyphSet = font.getGlyphSet()
    cmap = font.getBestCmap()
    gname = cmap.get(ord(ch), None)
    if not gname: return None
    glyph = glyphSet[gname]
    pen = GlyphToPolygonPen(glyphSet)
    glyph.draw(pen)
    polys = pen.get_polygons()
    if not polys: return None
    return unary_union(polys)

def _raster_skeleton_length(shape, raster=900):
    minx, miny, maxx, maxy = shape.bounds
    W = max(maxx - minx, maxy - miny)
    if W <= 0: return 0.0
    scale = raster / W
    # Rasterize by sampling centers of pixels quickly (vectorized)
    xs = np.linspace(minx, maxx, raster, endpoint=False) + (maxx-minx)/raster/2
    ys = np.linspace(miny, maxy, raster, endpoint=False) + (maxy-miny)/raster/2
    gridx, gridy = np.meshgrid(xs, ys)
    # Shapely vectorized contains is not available; do pointwise test
    mask = np.zeros((raster, raster), dtype=bool)
    # Chunk rows for speed
    for j in range(raster):
        row_pts = [ (float(gridx[j,i]), float(gridy[j,i])) for i in range(raster) ]
        mask[j,:] = [ shape.contains(Polygon([(px,py),(px+1e-6,py),(px,py+1e-6)])) for (px,py) in row_pts ]
    skel = skeletonize(mask)
    # Approximate path length: sum of 4-neighborhood differences scaled back
    ys_idx, xs_idx = np.where(skel)
    if len(xs_idx) < 2: return 0.0
    # Use local neighborhood stepping
    length_px = 0.0
    for i in range(len(xs_idx)-1):
        dx = xs_idx[i+1] - xs_idx[i]
        dy = ys_idx[i+1] - ys_idx[i]
        length_px += np.hypot(dx, dy)
    # Each pixel ~ (W/raster) units in font units
    return (length_px / scale)

def compute_lengths(font_bytes, text, letter_height_mm):
    with tempfile.TemporaryDirectory() as tmp:
        font_path = os.path.join(tmp, "font.ttf")
        with open(font_path, "wb") as f:
            f.write(font_bytes)
        font = TTFont(font_path)
        upm = font["head"].unitsPerEm

        per = []
        total_units = 0.0
        for ch in text:
            if ch == " ":
                per.append({"char": " ", "length_mm": 0.0})
                continue
            poly = _glyph_polygon(font, ch)
            if poly is None or poly.is_empty:
                per.append({"char": ch, "length_mm": 0.0})
                continue
            length_units = _raster_skeleton_length(poly, raster=900)
            length_mm = length_units * (letter_height_mm / upm)
            per.append({"char": ch, "length_mm": float(length_mm)})
            total_units += length_units

        total_mm = total_units * (letter_height_mm / upm)
        return per, float(total_mm)
