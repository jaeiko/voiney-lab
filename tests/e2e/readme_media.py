"""Compress the opt-in walk's real screenshots; never generate or retouch UI.

Run from the repository root after readme-capture.spec.ts, with the test extra
installed (Pillow is a runtime dependency). Raw PNGs stay outside the repository.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageOps

SCENES = ("01-upload", "02-pages", "03-review", "04-start", "05-run", "06-end", "07-notebook")
ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path.home() / "reports/readme-capture")
    args = parser.parse_args()
    output = ROOT / "docs/assets/readme"
    output.mkdir(parents=True, exist_ok=True)
    # Validate the complete input set before publishing any compressed image.
    inputs = [args.raw_dir / f"{device}-{scene}.png" for device in ("tablet", "phone") for scene in SCENES]
    for source in inputs:
        with Image.open(source) as picture:
            picture.verify()
    for source in inputs:
        destination = output / source.with_suffix(".webp").name
        with Image.open(source) as picture:
            picture.convert("RGB").save(destination, "WEBP", quality=86, method=6)
        if destination.stat().st_size > 300_000:
            raise ValueError(f"Image exceeds 300 KB: {destination.name}")
        print(f"{destination.name}: {destination.stat().st_size} bytes")

    frames = []
    for scene in SCENES:
        with Image.open(args.raw_dir / f"tablet-{scene}.png") as picture:
            # A tall notebook is fitted inside the same frame without cropping.
            frames.append(ImageOps.pad(picture.convert("RGB"), (1180, 820), color="white").quantize(colors=128))
    animation = output / "flow.gif"
    frames[0].save(animation, save_all=True, append_images=frames[1:], duration=2200, loop=0, optimize=True, disposal=2)
    with Image.open(animation) as picture:
        if picture.n_frames != len(SCENES):
            raise ValueError("Animation lost a scene")
    if animation.stat().st_size > 3_000_000:
        raise ValueError("Animation exceeds 3 MB")
    print(f"{animation.name}: {animation.stat().st_size} bytes; {len(frames)} frames")


if __name__ == "__main__":
    main()
