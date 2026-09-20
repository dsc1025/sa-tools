"""Verify source/action geometry against decoded Stone Age frames; render review images."""
import argparse
import json
import struct
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

from ro_sprite_preview import composite_frame, read_act, read_spr
from sa_resource import decode_rle, palette


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    colours = palette(Path("stoneage2.5/data/pal/Palet_1.sap"))
    source = read_spr(Path("C:/Work/RO/巴风特.spr"))
    actions, intervals = read_act(Path("C:/Work/RO/巴风特.act"), with_intervals=True)
    mapping = {0: 2, 1: 3, 2: 4, 3: 0, 4: 1, 9: 5, 10: 0}
    decoded = {}
    with zipfile.ZipFile(args.package) as z:
        manifest = json.loads(z.read("manifest.json"))
        for item in manifest["frames"]:
            number = item["number"]
            adrn, real = z.read(f"frames/{number}.adrn"), z.read(f"frames/{number}.real")
            _, _, size, x, y, width, height = struct.unpack_from("<IIIiiII", adrn)
            magic, compressed, rw, _, rh, _, total = struct.unpack_from("<2sHHHHHI", real)
            assert magic == b"RD" and size == total == len(real) and (rw, rh) == (width, height)
            pixels = decode_rle(real[16:], width * height) if compressed else real[16:]
            assert len(pixels) == width * height
            rgba = bytearray()
            for iy in range(height - 1, -1, -1):
                for value in pixels[iy * width:(iy + 1) * width]:
                    rgba.extend((*colours[value], 0 if value == 253 else 255))
            decoded[number] = (Image.frombytes("RGBA", (width, height), bytes(rgba)), (x, y))
        block = z.read("sprite/data.bin")
    cursor, references = 0, 0
    clips = {}
    while cursor < len(block):
        direction, code, duration, count = struct.unpack_from("<HHII", block, cursor)
        cursor += 12
        group = mapping[code]
        expected_frames = actions[group * 8 + direction]
        assert count == len(expected_frames)
        assert duration == round(intervals[group * 8 + direction] * 25 * count)
        clip = []
        for source_layers in expected_frames:
            bitmap, fx, fy, sound = struct.unpack_from("<IhhH", block, cursor)
            cursor += 10
            image, offset = decoded[bitmap]
            source_image, source_offset = composite_frame(source_layers, source)
            assert offset == source_offset
            assert image.height == source_image.height and image.width == (source_image.width + 3) & ~3
            expected_alpha = source_image.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0)
            actual_alpha = image.getchannel("A")
            assert actual_alpha.crop((0, 0, source_image.width, source_image.height)).tobytes() == expected_alpha.tobytes(), (direction, code, references)
            assert actual_alpha.crop((source_image.width, 0, image.width, image.height)).getbbox() is None
            assert fx == fy == sound == 0
            clip.append((image, offset))
            references += 1
        clips[direction, code] = clip
    assert len(clips) == 56
    args.output.mkdir(parents=True, exist_ok=True)

    def render(clip):
        left = min(offset[0] for image, offset in clip) - 20
        top = min(offset[1] for image, offset in clip) - 20
        right = max(offset[0] + image.width for image, offset in clip) + 20
        bottom = max(offset[1] + image.height for image, offset in clip) + 20
        result = []
        for image, offset in clip:
            canvas = Image.new("RGBA", (right - left, bottom - top), (60, 60, 60, 255))
            canvas.alpha_composite(image, (offset[0] - left, offset[1] - top))
            result.append(canvas.convert("RGB"))
        return result

    names = {0: "Attack", 1: "Hurt", 2: "Death", 3: "Idle", 4: "Move", 9: "Special", 10: "Guard (idle fallback)"}
    panels = []
    for code in mapping:
        frames = render(clips[0, code])
        frames[0].save(args.output / f"SA_{code}_direction_0.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
        selected = [frames[0], frames[len(frames) // 2], frames[-1]]
        panel = Image.new("RGB", (max(frame.width for frame in selected) * 3, max(frame.height for frame in selected) + 28), (60, 60, 60))
        ImageDraw.Draw(panel).text((8, 8), f"SA {code}: {names[code]}", fill="white")
        for index, frame in enumerate(selected):
            panel.paste(frame, (index * (panel.width // 3), 28))
        panels.append(panel)
    review = Image.new("RGB", (max(panel.width for panel in panels), sum(panel.height for panel in panels)), (60, 60, 60))
    y = 0
    for panel in panels:
        review.paste(panel, (0, y))
        y += panel.height
    review.save(args.output / "action_review.png")
    frames = render([clips[direction, 3][0] for direction in range(8)])
    directions = Image.new("RGB", (frames[0].width * 4, frames[0].height * 2 + 32), (60, 60, 60))
    for direction, frame in enumerate(frames):
        x, y = direction % 4 * frame.width, direction // 4 * (frame.height + 16)
        ImageDraw.Draw(directions).text((x + 4, y), f"Direction {direction}", fill="white")
        directions.paste(frame, (x, y + 16))
    directions.save(args.output / "idle_8_directions.png")
    report = {"action_mapping_SA_to_RO": mapping, "animation_blocks": len(clips), "references_verified": references, "unique_frames": len(decoded), "package_bytes": args.package.stat().st_size, "geometry_and_transparency": "all decoded frames match full source composites"}
    (args.output / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
