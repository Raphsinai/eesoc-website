"""Rasterise EESoc's official pins SVG for the dark photo-print background.

Optional developer dependency: pip install resvg-py
The generated PNG is committed; resvg is NOT a runtime API dependency.
Gold pins remain official gold; the blue lettering is made ivory for contrast.
"""
from pathlib import Path
import resvg_py

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "frontend/public/assets/logo-wordmark-pins.svg"
target = ROOT / "backend/api/assets/eesoc-pins-light.png"
svg = source.read_text().replace("#1049B1", "#F0F4FF")
target.write_bytes(resvg_py.svg_to_bytes(svg_string=svg, width=1400))
print("Rendered official pins logo to", target, target.stat().st_size, "bytes")
