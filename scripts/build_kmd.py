"""Turn a KMD 7-day forecast file (kmd/7day.txt) into kmd-data.js for the KMD outlook page.

The file is a grid of points (lon, lat, rain, min_temp, max_temp, ...), about 4 km apart.
It holds one value per point for the whole week: total rainfall, and the lowest minimum
and highest maximum temperature. For every county and ward this script averages all grid
points inside it, and finds the most extreme point.

This runs automatically when a new kmd/7day.txt is uploaded to GitHub
(see .github/workflows/kmd-update.yml).
"""
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
from matplotlib.path import Path as Poly

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "kmd" / "7day.txt"
SHAPES = ROOT / "data" / "shapes.json"
OUT = ROOT / "kmd-data.js"
EAT = timezone(timedelta(hours=3))
NODATA = -32768


def file_date():
    """When the KMD file was added: from GitHub's history if available, else the file's own date."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(SRC)], cwd=ROOT,
                             capture_output=True, text=True, timeout=20).stdout.strip()
        if out:
            return datetime.fromisoformat(out).astimezone(EAT)
    except Exception:
        pass
    return datetime.fromtimestamp(SRC.stat().st_mtime, EAT)


def rings(d):
    """Polygons from an SVG path of M/L/Z commands, in (lon, lat)."""
    out, cur = [], []
    for part in d.replace("Z", " Z ").replace("M", " M ").replace("L", " L ").split():
        if part in ("M", "L"):
            continue
        if part == "Z":
            if len(cur) > 2:
                out.append(np.array(cur))
            cur = []
            continue
        x, y = part.split(",")
        cur.append((float(x), -float(y)))   # map paths store latitude as -lat
    if len(cur) > 2:
        out.append(np.array(cur))
    return out


def main():
    head = SRC.open().readline().split()
    need = ["lon", "lat", "rain", "min_temp", "max_temp"]
    missing = [c for c in need if c not in head]
    if missing:
        raise RuntimeError(f"{SRC.name} is missing columns: {', '.join(missing)}")
    cols = [head.index(c) for c in need]
    raw = np.loadtxt(SRC, skiprows=1, usecols=cols)
    lon, lat = raw[:, 0], raw[:, 1]
    ulon, ulat = np.unique(np.round(lon, 3)), np.unique(np.round(lat, 3))
    nx, ny = len(ulon), len(ulat)
    # spacing from the full extent: the rounded coordinates make single steps look uneven
    dx, dy = float((ulon[-1] - ulon[0]) / (nx - 1)), float((ulat[-1] - ulat[0]) / (ny - 1))
    # the file rounds coordinates to 2 decimals, so look each one up rather than computing it
    ix = np.searchsorted(ulon, np.round(lon, 3))
    iy = np.searchsorted(ulat, np.round(lat, 3))
    grids = {}
    for k, name in enumerate(["rain", "tmin", "tmax"], start=2):
        g = np.full((ny, nx), np.nan)
        v = raw[:, k].copy()
        v[v < -900] = np.nan
        g[iy, ix] = v
        grids[name] = g

    # grid cell centres for point-in-polygon tests
    GX, GY = np.meshgrid(ulon, ulat)
    flat = np.column_stack([GX.ravel(), GY.ravel()])

    def summarise(d, label_pt=None):
        mask = np.zeros(flat.shape[0], bool)
        for ring in rings(d):
            lo, hi = ring.min(0), ring.max(0)
            sel = np.where((flat[:, 0] >= lo[0]) & (flat[:, 0] <= hi[0]) & (flat[:, 1] >= lo[1]) & (flat[:, 1] <= hi[1]))[0]
            if len(sel):
                mask[sel[Poly(ring).contains_points(flat[sel])]] = True
        if not mask.any():   # a ward smaller than one grid cell: use the nearest cell
            pts = np.vstack(rings(d)) if d else np.array([label_pt])
            cx, cy = pts.mean(0)
            mask[int(np.abs(ulat - cy).argmin()) * nx + int(np.abs(ulon - cx).argmin())] = True
        m = []
        for name in ("rain", "tmax", "tmin"):
            v = grids[name].ravel()[mask]
            v = v[~np.isnan(v)]
            if not len(v):
                m += [None, None]
                continue
            ext = v.min() if name == "tmin" else v.max()
            m += [round(float(v.mean()), 1), round(float(ext), 1)]
        return m, int(mask.sum())

    shapes = json.loads(SHAPES.read_text())
    counties = [summarise(c["d"])[0] for c in shapes["counties"]]
    wards = [summarise(w["d"])[0] for w in shapes["wards"]]

    def pack(g, signed):
        q = np.where(np.isnan(g), NODATA, np.rint(g * 10)).astype("<i2")
        return base64.b64encode(q[::-1].tobytes()).decode()   # rows from north to south

    issued = file_date()
    OUT.write_text("window.KMD = " + json.dumps({
        "received": issued.strftime("%Y-%m-%dT%H:%M%z"),
        "source": "Kenya Meteorological Department (KMD)",
        "grid": {"lon0": round(float(ulon[0]), 4), "lat_top": round(float(ulat[-1]), 4),
                 "dx": round(dx, 6), "dy": round(dy, 6), "nx": nx, "ny": ny, "nodata": NODATA,
                 "rain": pack(grids["rain"], False), "tmin": pack(grids["tmin"], True), "tmax": pack(grids["tmax"], True)},
        "counties": counties,
        "wards": wards,
    }, separators=(",", ":")) + ";\n")
    print(f"Wrote {OUT.name}: grid {nx}x{ny}, {len(counties)} counties, {len(wards)} wards, file dated {issued:%Y-%m-%d %H:%M} EAT")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"KMD update failed, kmd-data.js left unchanged: {e}", file=sys.stderr)
        sys.exit(1)
