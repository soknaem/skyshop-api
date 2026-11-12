"""Generates the demo product pictures (simple flat device illustrations) as SVG files.

    python scripts/gen_product_images.py
"""
import colorsys
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops_service"))
from ops_app.seed_data import PRODUCTS  # noqa: E402

OUT = ROOT / "frontend" / "public" / "products"
W = "#f8fafc"        # device body
D = "#0b1220"        # dark screen / details
G = "#ffffff" + ""  # used with opacity below


def hsl(h: int, s: int, l: int) -> str:
    """CSS-style hsl -> #rrggbb (hex works in every SVG renderer, including social previews)."""
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, l / 100, s / 100)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def shape(kind: str, h: int) -> str:
    glow = hsl(h, 90, 70)
    if kind == "phone":
        return (f'<rect x="290" y="130" width="220" height="440" rx="36" fill="{W}"/>'
                f'<rect x="304" y="146" width="192" height="408" rx="26" fill="{D}"/>'
                f'<rect x="304" y="146" width="192" height="408" rx="26" fill="url(#g2)" opacity=".85"/>'
                f'<rect x="372" y="156" width="56" height="12" rx="6" fill="{D}"/>'
                f'<circle cx="400" cy="360" r="46" fill="{glow}" opacity=".5"/>')
    if kind == "tablet":
        return (f'<rect x="190" y="170" width="420" height="320" rx="30" fill="{W}"/>'
                f'<rect x="206" y="186" width="388" height="288" rx="18" fill="{D}"/>'
                f'<rect x="206" y="186" width="388" height="288" rx="18" fill="url(#g2)" opacity=".85"/>'
                f'<circle cx="400" cy="330" r="50" fill="{glow}" opacity=".5"/>')
    if kind == "laptop":
        return (f'<rect x="210" y="200" width="380" height="250" rx="16" fill="{W}"/>'
                f'<rect x="224" y="214" width="352" height="222" rx="8" fill="{D}"/>'
                f'<rect x="224" y="214" width="352" height="222" rx="8" fill="url(#g2)" opacity=".85"/>'
                f'<path d="M150 462h500l-34 40H184z" fill="{W}"/><rect x="350" y="470" width="100" height="8" rx="4" fill="{G}" fill-opacity=".3"/>'
                f'<circle cx="400" cy="325" r="44" fill="{glow}" opacity=".5"/>')
    if kind == "headphones":
        return (f'<path d="M240 400v-60a160 160 0 0 1 320 0v60" fill="none" stroke="{W}" stroke-width="30" stroke-linecap="round"/>'
                f'<rect x="200" y="380" width="86" height="150" rx="38" fill="{W}"/><rect x="514" y="380" width="86" height="150" rx="38" fill="{W}"/>'
                f'<rect x="222" y="410" width="42" height="90" rx="21" fill="{D}" opacity=".7"/><rect x="536" y="410" width="42" height="90" rx="21" fill="{D}" opacity=".7"/>')
    if kind == "earbuds":
        return (f'<rect x="250" y="330" width="300" height="190" rx="70" fill="{W}"/><path d="M250 420h300" stroke="{G}" stroke-opacity=".3" stroke-width="6"/>'
                f'<circle cx="400" cy="470" r="10" fill="{glow}"/>'
                f'<g fill="{W}"><circle cx="330" cy="260" r="38"/><rect x="316" y="260" width="28" height="88" rx="14"/>'
                f'<circle cx="470" cy="260" r="38"/><rect x="456" y="260" width="28" height="88" rx="14"/></g>')
    if kind == "speaker":
        return (f'<rect x="230" y="260" width="340" height="190" rx="60" fill="{W}"/>'
                + "".join(f'<circle cx="{300 + i * 50}" cy="355" r="6" fill="{D}" opacity=".5"/>' for i in range(5))
                + f'<circle cx="400" cy="300" r="10" fill="{glow}"/><circle cx="400" cy="410" r="10" fill="{glow}"/>')
    if kind == "watch":
        return (f'<rect x="340" y="110" width="120" height="150" rx="30" fill="{W}" opacity=".85"/><rect x="340" y="540" width="120" height="150" rx="30" fill="{W}" opacity=".85"/>'
                f'<rect x="270" y="240" width="260" height="320" rx="70" fill="{W}"/><rect x="288" y="258" width="224" height="284" rx="56" fill="{D}"/>'
                f'<rect x="288" y="258" width="224" height="284" rx="56" fill="url(#g2)" opacity=".8"/>'
                f'<circle cx="400" cy="400" r="62" fill="none" stroke="{glow}" stroke-width="10"/><path d="M400 360v40l26 18" stroke="{W}" stroke-width="9" stroke-linecap="round" fill="none"/>')
    if kind == "console":
        return (f'<rect x="300" y="190" width="200" height="380" rx="26" fill="{W}"/><rect x="326" y="190" width="14" height="380" fill="{G}" fill-opacity=".3"/>'
                f'<circle cx="440" cy="250" r="14" fill="{glow}"/><rect x="390" y="320" width="70" height="8" rx="4" fill="{D}" opacity=".5"/><rect x="390" y="345" width="70" height="8" rx="4" fill="{D}" opacity=".5"/>')
    if kind == "controller":
        return (f'<path d="M230 330c40-50 300-50 340 0 40 50 60 170 20 190s-70-60-110-60H320c-40 0-70 80-110 60s-20-140 20-190z" fill="{W}"/>'
                f'<rect x="290" y="340" width="70" height="20" rx="10" fill="{D}" opacity=".6"/><rect x="315" y="315" width="20" height="70" rx="10" fill="{D}" opacity=".6"/>'
                f'<circle cx="470" cy="335" r="14" fill="{glow}"/><circle cx="505" cy="365" r="14" fill="{D}" opacity=".6"/><circle cx="440" cy="365" r="14" fill="{D}" opacity=".6"/><circle cx="470" cy="395" r="14" fill="{D}" opacity=".6"/>')
    if kind == "monitor":
        return (f'<rect x="170" y="190" width="460" height="290" rx="18" fill="{W}"/><rect x="184" y="204" width="432" height="262" rx="8" fill="{D}"/>'
                f'<rect x="184" y="204" width="432" height="262" rx="8" fill="url(#g2)" opacity=".85"/>'
                f'<rect x="380" y="480" width="40" height="70" fill="{W}"/><rect x="310" y="548" width="180" height="16" rx="8" fill="{W}"/>')
    if kind == "tv":
        return (f'<rect x="130" y="180" width="540" height="320" rx="14" fill="{W}"/><rect x="142" y="192" width="516" height="296" rx="6" fill="{D}"/>'
                f'<rect x="142" y="192" width="516" height="296" rx="6" fill="url(#g2)" opacity=".85"/>'
                f'<circle cx="400" cy="340" r="52" fill="{glow}" opacity=".5"/><path d="M300 500l-30 60M500 500l30 60" stroke="{W}" stroke-width="14" stroke-linecap="round"/>')
    if kind == "router":
        return (f'<rect x="220" y="360" width="360" height="110" rx="26" fill="{W}"/>'
                + "".join(f'<circle cx="{290 + i * 40}" cy="415" r="7" fill="{glow}"/>' for i in range(4))
                + f'<path d="M280 360l-30-130M400 360V210M520 360l30-130" stroke="{W}" stroke-width="14" stroke-linecap="round"/>')
    if kind == "camera":
        return (f'<rect x="180" y="260" width="440" height="280" rx="36" fill="{W}"/><rect x="250" y="220" width="110" height="52" rx="14" fill="{W}"/>'
                f'<circle cx="400" cy="400" r="104" fill="{D}"/><circle cx="400" cy="400" r="72" fill="url(#g2)" opacity=".9"/><circle cx="400" cy="400" r="30" fill="{glow}" opacity=".6"/>'
                f'<circle cx="560" cy="304" r="12" fill="{glow}"/>')
    if kind == "keyboard":
        keys = "".join(f'<rect x="{182 + c * 46}" y="{286 + r * 52}" width="38" height="38" rx="8" fill="{D}" opacity=".55"/>' for r in range(3) for c in range(10))
        return f'<rect x="160" y="250" width="480" height="210" rx="26" fill="{W}"/>{keys}<rect x="296" y="404" width="208" height="38" rx="8" fill="{D}" opacity=".55"/>'
    if kind == "mouse":
        return (f'<path d="M400 200c90 0 130 90 130 190 0 110-50 190-130 190s-130-80-130-190c0-100 40-190 130-190z" fill="{W}"/>'
                f'<path d="M400 200v150" stroke="{D}" stroke-width="6" opacity=".4"/><rect x="386" y="262" width="28" height="56" rx="14" fill="{glow}"/>')
    if kind == "powerbank":
        return (f'<rect x="290" y="170" width="220" height="400" rx="40" fill="{W}"/><rect x="320" y="210" width="160" height="14" rx="7" fill="{D}" opacity=".35"/>'
                + "".join(f'<rect x="{325 + i * 38}" y="480" width="26" height="46" rx="6" fill="{glow}" opacity="{.4 + i * .2}"/>' for i in range(4))
                + f'<path d="M410 290l-40 70h40l-20 70 60-90h-44l30-50z" fill="{glow}"/>')
    if kind == "charger":
        return (f'<rect x="300" y="240" width="200" height="260" rx="34" fill="{W}"/><rect x="350" y="170" width="16" height="70" rx="4" fill="{W}"/><rect x="434" y="170" width="16" height="70" rx="4" fill="{W}"/>'
                f'<rect x="340" y="400" width="120" height="26" rx="13" fill="{D}" opacity=".6"/><path d="M410 290l-34 60h34l-16 56 50-74h-36l24-42z" fill="{glow}"/>')
    if kind == "cable":
        return (f'<path d="M230 300c120-120 260 120 340 0" fill="none" stroke="{W}" stroke-width="18" stroke-linecap="round"/>'
                f'<rect x="170" y="270" width="80" height="60" rx="14" fill="{W}"/><rect x="550" y="270" width="80" height="60" rx="14" fill="{W}"/>'
                f'<rect x="190" y="290" width="40" height="20" rx="6" fill="{D}" opacity=".6"/><rect x="570" y="290" width="40" height="20" rx="6" fill="{D}" opacity=".6"/>')
    return ""


def svg(kind: str, hue: int) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800"><defs>'
            f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{hsl(hue, 72, 52)}"/><stop offset="1" stop-color="{hsl(hue + 35, 70, 22)}"/></linearGradient>'
            f'<linearGradient id="g2" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{hsl(hue, 85, 60)}"/><stop offset="1" stop-color="{hsl(hue + 50, 85, 35)}"/></linearGradient></defs>'
            f'<rect width="800" height="800" fill="url(#bg)"/><circle cx="640" cy="150" r="190" fill="#fff" opacity=".07"/><circle cx="130" cy="700" r="230" fill="#fff" opacity=".06"/>'
            f'<ellipse cx="400" cy="640" rx="210" ry="22" fill="#000" opacity=".18"/>{shape(kind, hue)}</svg>')


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for p in PRODUCTS:
        slug, kind, hue = p[0], p[7], p[8]
        (OUT / f"{slug}.svg").write_text(svg(kind, hue), encoding="utf-8")
    (OUT / "placeholder.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800"><rect width="800" height="800" fill="#e5e9f2"/>'
        '<rect x="300" y="300" width="200" height="200" rx="24" fill="none" stroke="#9aa6bd" stroke-width="12"/>'
        '<circle cx="360" cy="360" r="20" fill="#9aa6bd"/><path d="M310 480l60-70 40 40 30-30 50 60z" fill="#9aa6bd"/></svg>', encoding="utf-8")
    print(f"{len(PRODUCTS)} product images written to {OUT}")
