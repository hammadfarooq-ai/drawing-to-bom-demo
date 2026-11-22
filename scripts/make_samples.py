"""Generate three synthetic mechanical drawings (vector PDFs) in samples/.

    python scripts/make_samples.py

1. 01_bracket_BRK-1001.pdf      - simple part, 6061-T6, toleranced dims, hole callouts, finish note
2. 02_assembly_ASM-2040.pdf     - small assembly with a 5-line parts list
3. 03_problem_SMP-3307.pdf      - deliberate problems: blank revision, qty 0, ambiguous material,
                                  and an inch dimension on a mm drawing
All text is real vector text so PyMuPDF can extract it with coordinates.
"""
from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen.canvas import Canvas

OUT = Path(__file__).resolve().parent.parent / "samples"
W, H = landscape(A3)  # 1190.55 x 841.89 pt
MM = 72 / 25.4        # points per mm at 1:1
M = 22                # outer margin
LINE = 0.6
THICK = 1.4
COMPANY = "MERIDIAN MACHINE WORKS"


# ------------------------------------------------------------------ primitives

def arrow(c: Canvas, x, y, ang, size=7):
    a1, a2 = ang + math.radians(160), ang - math.radians(160)
    p = c.beginPath()
    p.moveTo(x, y)
    p.lineTo(x + size * math.cos(a1), y + size * math.sin(a1))
    p.lineTo(x + size * math.cos(a2), y + size * math.sin(a2))
    p.close()
    c.drawPath(p, fill=1, stroke=0)


def text(c: Canvas, x, y, s, size=9, font="Helvetica", anchor="left"):
    c.setFont(font, size)
    if anchor == "center":
        c.drawCentredString(x, y, s)
    elif anchor == "right":
        c.drawRightString(x, y, s)
    else:
        c.drawString(x, y, s)


def dim_h(c: Canvas, x1, x2, y_feat, y_dim, label, size=9):
    """Horizontal dimension between x1 and x2, dimension line at y_dim."""
    c.setLineWidth(0.4)
    ext = 3 if y_dim > y_feat else -3
    c.line(x1, y_feat + (2 if y_dim > y_feat else -2), x1, y_dim + ext)
    c.line(x2, y_feat + (2 if y_dim > y_feat else -2), x2, y_dim + ext)
    c.line(x1, y_dim, x2, y_dim)
    arrow(c, x1, y_dim, math.pi)
    arrow(c, x2, y_dim, 0)
    tw = c.stringWidth(label, "Helvetica", size)
    c.setFillColorRGB(1, 1, 1)
    c.rect((x1 + x2) / 2 - tw / 2 - 2, y_dim - 1, tw + 4, size + 2, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    text(c, (x1 + x2) / 2, y_dim + 2, label, size, anchor="center")


def dim_v(c: Canvas, y1, y2, x_feat, x_dim, label, size=9, x_feat2=None):
    """Vertical dimension; text horizontal (unidirectional) beside the line."""
    c.setLineWidth(0.4)
    sgn = 1 if x_dim > x_feat else -1
    x_feat2 = x_feat if x_feat2 is None else x_feat2
    c.line(x_feat + 2 * sgn, y1, x_dim + 3 * sgn, y1)
    c.line(x_feat2 + 2 * sgn, y2, x_dim + 3 * sgn, y2)
    c.line(x_dim, y1, x_dim, y2)
    arrow(c, x_dim, y1, -math.pi / 2 if y1 < y2 else math.pi / 2)
    arrow(c, x_dim, y2, math.pi / 2 if y1 < y2 else -math.pi / 2)
    tw = c.stringWidth(label, "Helvetica", size)
    ym = (y1 + y2) / 2 - size / 3
    c.setFillColorRGB(1, 1, 1)
    if sgn < 0:
        c.rect(x_dim - tw - 6, ym - 2, tw + 4, size + 3, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        text(c, x_dim - 4, ym, label, size, anchor="right")
    else:
        c.rect(x_dim + 2, ym - 2, tw + 4, size + 3, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
        text(c, x_dim + 4, ym, label, size)


def leader(c: Canvas, x, y, tx, ty, label, size=9):
    """Leader from feature point (x,y) to text at (tx,ty)."""
    c.setLineWidth(0.4)
    shelf = 14 if tx >= x else -14
    c.line(x, y, tx, ty)
    c.line(tx, ty, tx + shelf, ty)
    arrow(c, x, y, math.atan2(y - ty, x - tx))
    if shelf > 0:
        text(c, tx + shelf + 3, ty - size / 3, label, size)
    else:
        text(c, tx + shelf - 3, ty - size / 3, label, size, anchor="right")


def centerline(c: Canvas, x1, y1, x2, y2):
    c.saveState()
    c.setLineWidth(0.3)
    c.setDash([10, 3, 2, 3])
    c.line(x1, y1, x2, y2)
    c.restoreState()


def hidden(c: Canvas, x1, y1, x2, y2):
    c.saveState()
    c.setLineWidth(0.5)
    c.setDash([4, 2])
    c.line(x1, y1, x2, y2)
    c.restoreState()


def hole(c: Canvas, x, y, d):
    c.setLineWidth(THICK)
    c.circle(x, y, d / 2)
    centerline(c, x - d / 2 - 6, y, x + d / 2 + 6, y)
    centerline(c, x, y - d / 2 - 6, x, y + d / 2 + 6)


def balloon(c: Canvas, x, y, bx, by, n):
    c.setLineWidth(0.4)
    ang = math.atan2(y - by, x - bx)
    c.line(bx + 9 * math.cos(ang), by + 9 * math.sin(ang), x, y)
    c.circle(x, y, 1.6, fill=1)
    c.setLineWidth(0.8)
    c.circle(bx, by, 9)
    text(c, bx, by - 3.5, str(n), 10, "Helvetica-Bold", anchor="center")


# ------------------------------------------------------------------ sheet

def frame(c: Canvas):
    c.setLineWidth(THICK)
    c.rect(M, M, W - 2 * M, H - 2 * M)
    c.setLineWidth(LINE)
    inner = 10
    c.rect(M + inner, M + inner, W - 2 * (M + inner), H - 2 * (M + inner))
    # zone markers
    cols, rows = 8, 6
    cw, rh = (W - 2 * M) / cols, (H - 2 * M) / rows
    c.setLineWidth(0.4)
    for i in range(1, cols):
        x = M + i * cw
        c.line(x, M, x, M + inner)
        c.line(x, H - M, x, H - M - inner)
    for i in range(1, rows):
        y = M + i * rh
        c.line(M, y, M + inner, y)
        c.line(W - M, y, W - M - inner, y)
    for i in range(cols):
        x = M + (i + 0.5) * cw
        text(c, x, M + 3, str(cols - i), 6, anchor="center")
        text(c, x, H - M - 8, str(cols - i), 6, anchor="center")
    for i in range(rows):
        y = M + (i + 0.5) * rh - 2
        letter = "ABCDEF"[i]
        text(c, M + 5, y, letter, 6, anchor="center")
        text(c, W - M - 5, y, letter, 6, anchor="center")


TB_W, TB_H = 400, 150


def title_block(c: Canvas, f: dict):
    """Title block in the bottom-right corner. f keys: title, part_no, rev, scale, sheet,
    material, finish, tol, units, drawn, date, checked, approved."""
    x0 = W - M - 10 - TB_W
    y0 = M + 10
    c.setLineWidth(THICK)
    c.rect(x0, y0, TB_W, TB_H)
    c.setLineWidth(LINE)
    rows = [24, 22, 22, 22, 30, 30]  # bottom to top heights
    ys = [y0]
    for h in rows:
        ys.append(ys[-1] + h)

    def cell(cx, cy, cw, ch, label, value, vsize=9.5, bold=False):
        c.setLineWidth(LINE)
        c.rect(cx, cy, cw, ch)
        text(c, cx + 3, cy + ch - 7, label, 5.5)
        if value:
            text(c, cx + 4, cy + 4, value, vsize, "Helvetica-Bold" if bold else "Helvetica")

    # row 0 (bottom): drawn / date / checked / approved
    cell(x0, ys[0], 100, rows[0], "DRAWN", f["drawn"])
    cell(x0 + 100, ys[0], 90, rows[0], "DATE", f["date"])
    cell(x0 + 190, ys[0], 100, rows[0], "CHECKED", f.get("checked", ""))
    cell(x0 + 290, ys[0], 110, rows[0], "APPROVED", f.get("approved", ""))
    # row 1: general tolerance / units
    cell(x0, ys[1], 290, rows[1], "GENERAL TOLERANCE UNLESS OTHERWISE SPECIFIED", f["tol"], 8.5)
    cell(x0 + 290, ys[1], 110, rows[1], "UNITS", f["units"])
    # row 2: finish / projection
    cell(x0, ys[2], 290, rows[2], "FINISH", f["finish"])
    cell(x0 + 290, ys[2], 110, rows[2], "PROJECTION", "THIRD ANGLE", 7.5)
    # row 3: material
    cell(x0, ys[3], 400, rows[3], "MATERIAL", f["material"])
    # row 4: part number / rev / scale / sheet
    cell(x0, ys[4], 200, rows[4], "PART NO.", f["part_no"], 13, bold=True)
    cell(x0 + 200, ys[4], 60, rows[4], "REV", f["rev"], 13, bold=True)
    cell(x0 + 260, ys[4], 70, rows[4], "SCALE", f["scale"], 11)
    cell(x0 + 330, ys[4], 70, rows[4], "SHEET", f.get("sheet", "1 OF 1"), 10)
    # row 5 (top): company + title
    cell(x0, ys[5], 150, rows[5], "", "")
    text(c, x0 + 75, ys[5] + 17, "MERIDIAN", 10, "Helvetica-Bold", anchor="center")
    text(c, x0 + 75, ys[5] + 6, "MACHINE WORKS", 7, anchor="center")
    cell(x0 + 150, ys[5], 250, rows[5], "TITLE", f["title"], 13, bold=True)
    return x0, y0 + TB_H


def notes(c: Canvas, x, y, lines, size=8.5):
    text(c, x, y, "NOTES:", 9.5, "Helvetica-Bold")
    for i, ln in enumerate(lines, start=1):
        text(c, x, y - 14 * i, f"{i}.  {ln}", size)


def rev_table(c: Canvas, revs):
    """Revision history block in the top-right."""
    widths = [34, 230, 70, 36]
    x0 = W - M - 10 - sum(widths)
    y_top = H - M - 10
    rh = 15
    c.setLineWidth(LINE)
    hdr = ["REV", "DESCRIPTION", "DATE", "BY"]
    rows = [hdr] + revs
    for r, row in enumerate(rows):
        y = y_top - (r + 1) * rh
        x = x0
        for i, w in enumerate(widths):
            c.rect(x, y, w, rh)
            text(c, x + 3, y + 4.5, row[i], 7 if r else 6.5, "Helvetica-Bold" if r == 0 else "Helvetica")
            x += w
    text(c, x0, y_top - (len(rows) + 1) * rh + 6, "REVISIONS", 6.5, "Helvetica-Bold")


def table(c: Canvas, x0, y_top, widths, header, rows, title=None, rh=16):
    c.setLineWidth(LINE)
    all_rows = [header] + rows
    for r, row in enumerate(all_rows):
        y = y_top - (r + 1) * rh
        x = x0
        for i, w in enumerate(widths):
            c.setLineWidth(THICK if r == 0 else LINE)
            c.rect(x, y, w, rh)
            val = row[i]
            font = "Helvetica-Bold" if r == 0 else "Helvetica"
            size = 7 if r == 0 else 8
            if i == 0 or (i == len(widths) - 1):
                text(c, x + w / 2, y + 5, val, size, font, anchor="center")
            else:
                text(c, x + 4, y + 5, val, size, font)
            x += w
    if title:
        text(c, x0, y_top + 4, title, 8.5, "Helvetica-Bold")
    return y_top - len(all_rows) * rh


# ------------------------------------------------------------------ sample 1

def bracket(path: Path):
    c = Canvas(str(path), pagesize=(W, H))
    c.setTitle("BRK-1001 Mounting Bracket")
    frame(c)
    s = MM * 1.0  # 1:1
    # FRONT VIEW: 120 x 80 plate with R5 corners
    fx, fy = 150, 330
    pw, ph = 120 * s, 80 * s
    c.setLineWidth(THICK)
    c.roundRect(fx, fy, pw, ph, 5 * s)
    # 2X Ø8.50 holes, 15 from each side, 60 from bottom
    h1x, h2x, hy = fx + 15 * s, fx + 105 * s, fy + 60 * s
    hole(c, h1x, hy, 8.5 * s)
    hole(c, h2x, hy, 8.5 * s)
    # Ø25 H7 centre bore at (60, 30)
    bx, by = fx + 60 * s, fy + 30 * s
    hole(c, bx, by, 25 * s)
    # bend line (flange at the bottom, seen as hidden line)
    hidden(c, fx, fy + 6 * s, fx + pw, fy + 6 * s)
    text(c, fx + pw / 2, fy - 75, "FRONT VIEW", 9, "Helvetica-Bold", anchor="center")

    dim_h(c, fx, fx + pw, fy, fy - 45, "120.00 ±0.10")
    dim_v(c, fy, fy + ph, fx, fx - 40, "80.00 ±0.10")
    dim_h(c, h1x, h2x, fy + ph, fy + ph + 40, "90.00 ±0.05")
    dim_h(c, fx, h1x, fy + ph, fy + ph + 18, "15.00", 8)
    dim_v(c, fy, hy, fx + pw, fx + pw + 30, "60.00 ±0.10")
    leader(c, h2x + 8.5 * s / 2 * 0.7, hy + 8.5 * s / 2 * 0.7, h2x + 50, hy + 60, "2X Ø8.50 THRU")
    leader(c, bx + 25 * s / 2 * 0.7, by - 25 * s / 2 * 0.7, fx + pw + 45, fy - 22, "Ø25.00 H7 THRU")

    # SIDE VIEW: L profile, vertical leg 80 high, flange 40 long, t = 6, inside R6
    sx, sy = 640, 330
    t, leg, fl, r_in = 6 * s, 80 * s, 40 * s, 6 * s
    r_out = r_in + t
    c.setLineWidth(THICK)
    p = c.beginPath()
    p.moveTo(sx, sy + leg)
    p.lineTo(sx, sy + r_out)
    p.arcTo(sx, sy, sx + 2 * r_out, sy + 2 * r_out, startAng=180, extent=90)
    p.lineTo(sx + fl, sy)
    p.lineTo(sx + fl, sy + t)
    p.lineTo(sx + t + r_in, sy + t)
    p.arcTo(sx + t, sy + t, sx + t + 2 * r_in, sy + t + 2 * r_in, startAng=270, extent=-90)
    p.lineTo(sx + t, sy + leg)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    text(c, sx + fl / 2, sy - 75, "SIDE VIEW", 9, "Helvetica-Bold", anchor="center")
    dim_h(c, sx, sx + t, sy + leg, sy + leg + 28, "6.00 ±0.05", 8)
    dim_h(c, sx, sx + fl, sy, sy - 45, "40.00 ±0.20")
    dim_v(c, sy, sy + leg, sx + t, sx + t + 60, "80.00 REF", 8)
    leader(c, sx + t + r_in * 0.3, sy + t + r_in * 0.3, sx - 60, sy + 120, "R6 INSIDE")
    # angle symbol near the bend
    ra = 62
    c.setLineWidth(0.4)
    c.arc(sx - ra, sy - ra, sx + ra, sy + ra, startAng=0, extent=90)
    arrow(c, sx + ra, sy, math.pi / 2 + 0.05)
    arrow(c, sx, sy + ra, math.pi - 0.05)
    text(c, sx + ra * 0.72 + 4, sy + ra * 0.72 + 2, "90° ±0.5°", 9)

    notes(c, M + 30, 190, [
        "BREAK ALL SHARP EDGES 0.2 MAX.",
        "FINISH: CLEAR ANODIZE PER MIL-A-8625 TYPE II, CLASS 1.",
        "SURFACE FINISH Ra 3.2 µm UNLESS OTHERWISE SPECIFIED.",
        "DIMENSIONS APPLY AFTER FINISH.",
        "REMOVE ALL BURRS. NO TOOL MARKS ON VISIBLE FACES.",
    ])
    rev_table(c, [["B", "INITIAL RELEASE", "2025-11-02", "JM"],
                  ["C", "TIGHTENED HOLE SPACING TOLERANCE", "2026-03-14", "JM"]])
    title_block(c, {
        "title": "MOUNTING BRACKET", "part_no": "BRK-1001", "rev": "C", "scale": "1:1",
        "material": "6061-T6 ALUMINUM", "finish": "CLEAR ANODIZE",
        "tol": "X.X ±0.2    X.XX ±0.05    ANGLES ±0.5°", "units": "MM",
        "drawn": "J. MARTINEZ", "date": "2026-03-14", "checked": "R. OKAFOR", "approved": "S. LINDQVIST",
    })
    c.showPage()
    c.save()


# ------------------------------------------------------------------ sample 2

def assembly(path: Path):
    c = Canvas(str(path), pagesize=(W, H))
    c.setTitle("ASM-2040 Roller Guide Assembly")
    frame(c)
    s = MM * 1.25  # drawn at 5:4
    ox, oy = 170, 330
    L, base_t = 160 * s, 10 * s
    up_w, up_h = 12 * s, 85 * s
    c.setLineWidth(THICK)
    # base plate (1)
    c.rect(ox, oy, L, base_t)
    # side supports (2)
    lx = ox + 20 * s
    rx = ox + L - 20 * s - up_w
    c.rect(lx, oy + base_t, up_w, up_h)
    c.rect(rx, oy + base_t, up_w, up_h)
    # shaft (3) across at height 62
    shaft_y = oy + 62 * s
    c.rect(lx - 4 * s, shaft_y - 6 * s, (rx + up_w + 4 * s) - (lx - 4 * s), 12 * s)
    centerline(c, lx - 15 * s, shaft_y, rx + up_w + 15 * s, shaft_y)
    # roller (4) dia 40 length 30 (side view as rectangle)
    rcx = ox + L / 2
    c.rect(rcx - 15 * s, shaft_y - 20 * s, 30 * s, 40 * s)
    # screws (5) through base into uprights
    for x in (lx + up_w / 2, rx + up_w / 2):
        hidden(c, x - 3 * s, oy, x - 3 * s, oy + base_t + 14 * s)
        hidden(c, x + 3 * s, oy, x + 3 * s, oy + base_t + 14 * s)
        c.setLineWidth(THICK)
        c.rect(x - 5 * s, oy - 6 * s, 10 * s, 6 * s)
    text(c, ox + L / 2, oy - 82, "FRONT VIEW  (SCALE 5:4)", 9, "Helvetica-Bold", anchor="center")

    dim_h(c, ox, ox + L, oy, oy - 55, "160.0 ±0.5")
    dim_v(c, oy, oy + base_t + up_h, ox, ox - 45, "95.0 ±0.5", x_feat2=lx)
    dim_v(c, oy, shaft_y, ox + L, ox + L + 40, "62.00 ±0.10")
    dim_h(c, lx + up_w, rx, oy + base_t + up_h, oy + base_t + up_h + 28, "100.00 +0.20/-0.00")
    leader(c, rcx + 15 * s, shaft_y + 14 * s, rcx + 22, shaft_y + 105, "ROLLER Ø40.00 ±0.05")
    leader(c, rx + up_w + 4 * s, shaft_y + 3 * s, rx + up_w + 80, shaft_y + 70, "SHAFT Ø12 h6")

    balloon(c, ox + 6 * s, oy + base_t / 2, ox - 30, oy + 40, 1)
    balloon(c, lx + 2, oy + base_t + up_h - 15, lx - 50, oy + base_t + up_h + 10, 2)
    balloon(c, lx - 2 * s, shaft_y, lx - 60, shaft_y + 40, 3)
    balloon(c, rcx - 10 * s, shaft_y - 18 * s, rcx - 90, oy + 30, 4)
    balloon(c, rx + up_w / 2 - 5 * s, oy - 4 * s, rx - 40, oy - 30, 5)

    # parts list directly above the title block
    tb_x, tb_top = W - M - 10 - TB_W, M + 10 + TB_H
    widths = [32, 78, 150, 106, 34]
    table(c, tb_x, tb_top + 16 * 6 + 2, widths,
          ["ITEM", "PART NUMBER", "DESCRIPTION", "MATERIAL", "QTY"],
          [["1", "BRK-2041", "BASE PLATE", "6061-T6", "1"],
           ["2", "BRK-2042", "SIDE SUPPORT", "6061-T6", "2"],
           ["3", "SHF-2043", "ROLLER SHAFT Ø12 h6", "AISI 304", "1"],
           ["4", "ROL-2044", "ROLLER Ø40 x 30", "POM (ACETAL)", "1"],
           ["5", "SCR-M6-16", "SOCKET HEAD CAP SCREW M6x16", "STEEL CL 12.9", "4"]],
          title="PARTS LIST")

    notes(c, M + 30, 190, [
        "TORQUE ITEM 5 SCREWS TO 10 N·m.",
        "APPLY LOCTITE 243 TO ALL SCREW THREADS.",
        "ROLLER (ITEM 4) MUST ROTATE FREELY AFTER ASSEMBLY, NO AXIAL PLAY > 0.1.",
        "ITEMS 1 AND 2: BLACK ANODIZE PER MIL-A-8625 TYPE II.",
    ])
    rev_table(c, [["A", "INITIAL RELEASE", "2026-05-20", "PK"]])
    title_block(c, {
        "title": "ROLLER GUIDE ASSEMBLY", "part_no": "ASM-2040", "rev": "A", "scale": "5:4",
        "material": "SEE PARTS LIST", "finish": "BLACK ANODIZE (ITEMS 1, 2)",
        "tol": "X.X ±0.5    X.XX ±0.10    ANGLES ±1°", "units": "MM",
        "drawn": "P. KOWALSKI", "date": "2026-05-20", "checked": "J. MARTINEZ",
    })
    c.showPage()
    c.save()


# ------------------------------------------------------------------ sample 3

def problem(path: Path):
    c = Canvas(str(path), pagesize=(W, H))
    c.setTitle("SMP-3307 Sensor Mount Plate")
    frame(c)
    s = MM * 2.0  # 2:1
    ox, oy = 170, 330
    pw, ph = 90 * s, 60 * s
    c.setLineWidth(THICK)
    c.rect(ox, oy, pw, ph)
    # 4X Ø4.50 holes on 76 x 46 pattern
    hx = [ox + 7 * s, ox + 83 * s]
    hy = [oy + 7 * s, oy + 53 * s]
    for x in hx:
        for y in hy:
            hole(c, x, y, 4.5 * s)
    # sensor slot, 2.50" long (deliberately inch on a mm drawing)
    sl, sw = 63.5 * s, 8 * s
    scx, scy = ox + pw / 2, oy + ph / 2
    c.roundRect(scx - sl / 2, scy - sw / 2, sl, sw, sw / 2)
    centerline(c, scx - sl / 2 - 10, scy, scx + sl / 2 + 10, scy)
    text(c, ox + pw / 2, oy - 78, "TOP VIEW  (SCALE 2:1)", 9, "Helvetica-Bold", anchor="center")

    dim_h(c, ox, ox + pw, oy, oy - 50, "90.00 ±0.10")
    dim_v(c, oy, oy + ph, ox, ox - 45, "60.00 ±0.10")
    dim_h(c, hx[0], hx[1], oy + ph, oy + ph + 40, "76.00 ±0.05")
    dim_v(c, hy[0], hy[1], ox + pw, ox + pw + 40, "46.00 ±0.05")
    dim_h(c, scx - sl / 2, scx + sl / 2, scy + sw / 2, oy + ph + 18, '2.50"', 8)
    leader(c, hx[1] + 4.5 * s / 2 * 0.7, hy[0] - 4.5 * s / 2 * 0.7, hx[1] + 60, oy - 25, "4X Ø4.50 THRU")
    leader(c, scx + sl / 2 - sw / 2, scy - sw / 2, scx + 40, oy + 25, "SLOT WIDTH 8.00 +0.1")

    # edge view (thickness)
    ex = ox + pw + 120
    c.setLineWidth(THICK)
    c.rect(ex, oy, 5 * s, ph)
    dim_h(c, ex, ex + 5 * s, oy + ph, oy + ph + 28, "5.00 ±0.05", 8)
    text(c, ex + 2.5 * s, oy - 22, "SIDE VIEW", 9, "Helvetica-Bold", anchor="center")

    tb_x, tb_top = W - M - 10 - TB_W, M + 10 + TB_H
    widths = [32, 78, 160, 96, 34]
    table(c, tb_x, tb_top + 16 * 5 + 2, widths,
          ["ITEM", "PART NUMBER", "DESCRIPTION", "MATERIAL", "QTY"],
          [["1", "SMP-3307", "SENSOR MOUNT PLATE", "SEE NOTE 4", "1"],
           ["2", "STO-M4-10", "HEX STANDOFF M4 x 10", "BRASS", "0"],
           ["3", "SCR-M4-08", "PAN HEAD SCREW M4x8", "A2 STAINLESS", "4"],
           ["4", "WSH-M4", "FLAT WASHER M4", "A2 STAINLESS", "4"]],
          title="HARDWARE KIT (SHIPS WITH PART)")

    notes(c, M + 30, 190, [
        "BREAK ALL SHARP EDGES.",
        "THREAD INSERTS NOT REQUIRED.",
        "SURFACE FINISH Ra 1.6 µm ON SENSOR FACE.",
        "MATERIAL TBD - ALUMINUM OR STEEL, CONFIRM WITH CUSTOMER BEFORE ORDER.",
    ])
    title_block(c, {
        "title": "SENSOR MOUNT PLATE", "part_no": "SMP-3307", "rev": "", "scale": "2:1",
        "material": "ALUMINUM OR STEEL (SEE NOTE 4)", "finish": "NONE",
        "tol": "X.X ±0.2    X.XX ±0.05", "units": "MM",
        "drawn": "T. NAKAMURA", "date": "2026-08-09",
    })
    c.showPage()
    c.save()


def main():
    OUT.mkdir(exist_ok=True)
    for fn, name in [(bracket, "01_bracket_BRK-1001.pdf"), (assembly, "02_assembly_ASM-2040.pdf"), (problem, "03_problem_SMP-3307.pdf")]:
        fn(OUT / name)
        print("wrote", OUT / name)


if __name__ == "__main__":
    main()
