# Optional GitHub README Motion

This workflow is adapted from oil-oil's MIT-licensed `beautify-github-readme`. Use motion only when it explains a sequence, transition, state change, or relationship. GIF is opt-in and never the default because GitHub does not play animation embedded inside SVG.

## Contents

1. [Gate](#gate)
2. [Motion defaults](#motion-defaults)
3. [Motion spec](#motion-spec)
4. [Render](#render)
5. [Verify](#verify)
6. [Upstream license](#upstream-license)

## Gate

1. Finish and approve a static SVG.
2. Confirm the motion has a communication job rather than decorative movement.
3. Ask once whether the user wants a GitHub-safe GIF while keeping the SVG source.
4. If the user declines, does not answer, or dependencies are unavailable, deliver static SVG only.

Do not replace a README image reference without separate approval.

## Motion Defaults

- Animate one to three semantic layers.
- Use short, related entry travel around `4–8%` of the canvas dimension.
- Use calm ease-out movement around `0.7–1.2` seconds.
- Hold the settled composition for roughly `1.5–2.5` seconds.
- Keep settled layers pixel-still; avoid idle bobbing, pulsing, floating, or rotation.
- Make the last frame return cleanly to the first.
- Start at `30 FPS`, `4–6` seconds, and the SVG's native width.
- Aim for about `2 MB`; treat `5 MB` as a practical ceiling.
- Avoid flashes, rapid pulses, and motion that competes with reading.

## Motion Spec

Give animated SVG elements stable IDs and keep inherited transforms and typography on ancestor groups.

Create a JSON file next to the SVG:

```json
{
  "width": 1200,
  "fps": 30,
  "duration": 5.0,
  "colors": 256,
  "dither": "none",
  "clip_to_base_alpha": true,
  "max_size_mb": 2.0,
  "reveals": [
    {
      "id": "title-highlight",
      "axis": "x",
      "start": 0.25,
      "end": 1.25,
      "exit": {"start": 4.1, "end": 4.96}
    }
  ],
  "layers": [
    {
      "id": "project-card",
      "enter": {"start": 0.4, "end": 1.3, "from": [72, -22]},
      "exit": {"start": 4.1, "end": 4.96, "to": [20, -10]}
    }
  ]
}
```

Offsets use source-SVG units and scale with output width. Use `clip_to_base_alpha: true` when moving layers must stay inside a rounded opaque frame.

## Render

The bundled renderer requires Python with Pillow, `ffmpeg`, and either `rsvg-convert` or macOS `sips`:

```bash
python3 scripts/render_motion_gif.py \
  assets/readme/hero.svg \
  assets/readme/hero.gif \
  --spec assets/readme/hero-motion.json
```

Use `--keep-frames /tmp/readme-motion-frames` only for frame debugging.

For flat graphics:

- Prefer the native SVG width or a clean integer scale.
- Start with 192 colors; use 256 for prominent text, gradients, or translucency.
- Use no dithering for flat fills, text, and UI geometry.
- Keep settled frames identical for better compression.
- Preserve transparent corners and a stable transparent silhouette.
- If a full-width animation stays too large, keep the hero static and use a smaller demonstration GIF later.

## Verify

1. Inspect entry, first settled frame, full hold, exit, and loop boundary.
2. Confirm settled frames are pixel-identical.
3. Preview at GitHub desktop and narrow widths.
4. Verify frames, FPS, duration, dimensions, and size with `ffprobe`.
5. Keep SVG and motion JSON beside the derived GIF.
6. Fall back to static SVG on dependency, rendering, legibility, loop, or file-size failure.

## Upstream License

The bundled `audit_readme.py` and `render_motion_gif.py` are adapted from:

`https://github.com/oil-oil/beautify-github-readme`

```text
MIT License

Copyright (c) 2026 oil-oil

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
