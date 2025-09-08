from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from skimage.morphology import skeletonize
import numpy as np
from PIL import Image, ImageDraw
import tempfile, os

class GlyphToPolygonPen(BasePen):
    def __init__(self, glyphSet):
        super().__init__(glyphSet)
        self.points = []
        self.cur = []

    def _moveTo(self, p):
        if self.cur:
            self.points.append(self.cur)
            self.cur = []
        self.cur.append(p)

    def _lineTo(self, p):
        self.cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        steps = 24
        x0, y0 = self.cur[-1]
        for t in np.linspace(0, 1, steps, endpoint=True):
            x = (1-t)**3*x0 + 3*(1-t)**2*t*p1[0] + 3*(1-t)*t**2*p2[0] + t**3*p3[0]
            y = (1-t)**3*y0 + 3*(1-t)**2*t*p1[1] + 3*(1-t)*t**2*p2[1] + t**3*p3[1]
            self.cur.append((x, y))

    def _closePath(self):
        if self.cur:
            self.points.append(self.cur)
            self.cur = []

    def get_polygons(self):
        # Return list of Shapely Polygons for the contours
        return [Polygon(c) for c in self.points if len(c) > 2]

def _glyph_polygon(font, ch):
    glyphSet = font.getGlyphSet()
    cmap = font.getBestCmap()
    gname = cmap.get(ord(ch), None)
    if not gname:
        return None
    pen = GlyphToPolygonPen(glyphSet)
    glyphSet[gname].draw(pen)
    polys = pen.get_polygons()
    if not polys:
        return None
    return unary_union(polys)

def _rasterize_polygon_fast(shape, raster=400):
    """Rasterize a (Multi)Polygon to a binary numpy array using Pillow (fast)."""
    if shape.is_empty:
        return np.zeros((raster, raster), dtype=bool)

    minx, miny, maxx, maxy = shape.bounds
    W = max(maxx - minx, maxy - miny)
    if W <= 0:
        return np.zeros((raster, raster), dtype=bool)

    # Normalize to a square raster canvas [0, raster]
    sx = sy = (raster - 2) / W  # leave 1px padding
    ox = -minx * sx + 1
    oy = -miny * sy + 1

    def tx(pt):
        x, y = pt
        return (x * sx + ox, (maxy - (y)) * sy + 1)  # flip Y for image coordinates

    img = Image.new("1", (raster, raster), 0)
    draw = ImageDraw.Draw(img)

    if isinstance(shape, MultiPolygon):
        polys = list(shape.geoms)
    else:
        polys = [shape]

    for poly in polys:
        # Exterior
        ext = [tx(p) for p in poly.exterior.coords]
        draw.polygon(ext, fill=1)
        # Holes
        for hole in poly.interiors:
            inter = [tx(p) for p in hole.coords]
            draw.polygon(inter, fill=0)

    return np.array(img, dtype=bool)

def _skeleton_length(mask, scale_units_per_pixel):
    skel = skeletonize(mask)
    ys, xs = np.where(skel)
    if len(xs) < 2:
        return 0.0
    # Approximate length by neighbor stepping
    length_px = 0.0
    for i in range(len(xs) - 1):
        dx = xs[i+1] - xs[i]
        dy = ys[i+1] - ys[i]
        length_px += (dx*dx + dy*dy) ** 0.5
    return float(length_px * scale_units_per_pixel)

def compute_lengths(font_bytes, text, letter_height_mm):
    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, "font.ttf")
        with open(fp, "wb") as f:
            f.write(font_bytes)
        font = TTFont(fp)
        upm = font["head"].unitsPerEm

        per = []
        total_units = 0.0

        for ch in text:
            if ch == " ":
                per.append({"char": " ", "length_mm": 0.0})
                continue

            shape = _glyph_polygon(font, ch)
            if shape is None or shape.is_empty:
                per.append({"char": ch, "length_mm": 0.0})
                continue

            # Rasterize quickly
            raster = 400  # keep modest so it’s fast on free tier; can raise later
            mask = _rasterize_polygon_fast(shape, raster=raster)

            # Convert pixel length to font units:
            minx, miny, maxx, maxy = shape.bounds
            W = max(maxx - minx, maxy - miny)
            if W <= 0:
                per.append({"char": ch, "length_mm": 0.0})
                continue
            units_per_pixel = W / raster

            length_units = _skeleton_length(mask, units_per_pixel)
            length_mm = float(length_units * (letter_height_mm / upm))
            per.append({"char": ch, "length_mm": length_mm})
            total_units += length_units

        total_mm = float(total_units * (letter_height_mm / upm))
        return per, total_mm
