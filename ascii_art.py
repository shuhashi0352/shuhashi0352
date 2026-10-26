import argparse
from pathlib import Path

from PIL import Image, ImageOps

RAMP = " .'`^,:;!|ijlt1fjrxnuvczkhmpqdbwgHM%&@"


def convert(path: str, width: int, invert: bool, gamma: float) -> list[str]:
    src = ImageOps.exif_transpose(Image.open(path))
    alpha = src.getchannel("A") if src.mode in ("RGBA", "LA") else None
    img = src.convert("L")
    img = ImageOps.autocontrast(img, cutoff=2)
    if invert:
        img = ImageOps.invert(img)
    height = max(1, round(img.height / img.width * width * 0.5))
    img = img.resize((width, height), Image.LANCZOS)
    if alpha is not None:
        alpha = alpha.resize((width, height), Image.LANCZOS)

    n = len(RAMP) - 1
    lines = []
    for y in range(height):
        row = ""
        for x in range(width):
            if alpha is not None and alpha.getpixel((x, y)) < 128:
                row += " "
                continue
            v = (img.getpixel((x, y)) / 255) ** gamma
            row += RAMP[round(v * n)]
        lines.append(row.rstrip())
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--width", type=int, default=40)
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("-o", "--output", default="ascii.txt")
    args = ap.parse_args()

    lines = convert(args.image, args.width, args.invert, args.gamma)
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
