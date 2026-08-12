# Optional GitHub README Motion

This workflow is adapted from oil-oil's MIT-licensed `beautify-github-readme`. Use motion only when it explains a sequence, transition, state change, or relationship. GIF is opt-in and never the default: static SVG is cheaper, deterministic, and always readable, while every animated asset must still satisfy the [Static Frame Contract](#static-frame-contract).

## Contents

1. [Gate](#gate)
2. [Motion defaults](#motion-defaults)
3. [GitHub playback matrix](#github-playback-matrix)
4. [Static frame contract](#static-frame-contract)
5. [Motion spec](#motion-spec)
6. [Render](#render)
7. [Verify](#verify)
8. [Demo recording](#demo-recording)
9. [Upstream license](#upstream-license)

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
- Aim for at most `2 MB`; the renderer enforces the same `max_size_mb: 2.0` budget.
- Avoid flashes, rapid pulses, and motion that competes with reading.

## GitHub Playback Matrix

GitHub's README layer renders SVG through an `<img>` tag and does not sanitize
file content: the `?sanitize=true` parameter is a no-op. Script execution is
blocked by a server-side CSP (`default-src 'none'; style-src 'unsafe-inline';
sandbox`), but animation is not. Dual-engine playback testing on 2026-08-12
confirmed both SVG animation engines play in current Chrome and Firefox:

| Engine | Technique | Chrome 151 | Firefox 153 |
| --- | --- | --- | --- |
| SMIL | `<animate>` / `<animateTransform>` | plays | plays |
| CSS | `@keyframes` | plays | plays |

Both engines animate in both browsers through GitHub README `<img>` rendering.
GIF therefore stays opt-in for legibility, size, and deterministic frames — not
because SVG animation is impossible. Re-verify the matrix with
`skill/scripts/verify_animation_matrix.py` before relying on a new engine
combination.

## Static Frame Contract

Any animated asset's frozen frame must be explicitly designed, never an
accidental intermediate:

- Design the first settled frame before adding motion, and verify it in the
  rendered output.
- A discrete animation frozen before its first transition can land on a
  `width="0"` empty bar; the frozen frame must read correctly on its own.
- Reduced-motion users, static fallbacks, and failed renders see exactly the
  frozen frame — never rely on motion to communicate the asset's meaning.

### Static Frame Generator

The animated route (`diagram_route: "animated"` with `static_frame: true`)
derives its still asset with `skill/scripts/render_static_frame.py`:

- `render_static_frame(svg, motion_spec, variant="settled") -> bytes` returns a
  static SVG whose geometry is the settled composition — the animation end
  state, never the `t=0` empty frame.  SMIL `<animate>`/`<set>`/`<animateTransform>`
  final values (`to`, or the last `values` entry) are baked into the target
  attributes and the animation elements removed; CSS `@keyframes` blocks and
  `animation` declarations are stripped so the authored settled values show.
- The settled state keys off the SVG's own animation declarations, never a
  motion JSON that may sit stale beside a fallback GIF: the spec is validated
  and cross-checked (every scene id must exist in the SVG) but never trusted
  for geometry.
- The animated route records this asset as `*-static.svg` (base-set asset form:
  role `animation`/`hero`, no scene/gate hash), satisfying the route contract
  that the animated route always carries a still frame.

```bash
python3 scripts/render_static_frame.py \
  assets/readme/hero.svg \
  assets/readme/hero-static.svg \
  --spec assets/readme/hero-motion.json
```

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

## Motion Spec v2

Motion Spec v2 (`schema_version: 2`) replaces `reveals`/`layers` with a scene contract plus a typewriter reveal. Project Timeline v1 or v2 with `project_motion_spec_v2()`, or author the JSON directly:

```json
{
  "schema_version": 2,
  "width": 1200,
  "fps": 30,
  "duration": 8.0,
  "colors": 192,
  "dither": "none",
  "max_size_mb": 2.0,
  "scenes": [
    {
      "id": "project-card",
      "interpolation": "linear",
      "enter": {"start": 0.2, "end": 0.9},
      "hold": {"start": 0.9, "end": 6.9},
      "exit": {"start": 6.9, "end": 7.6}
    }
  ],
  "typewriter": {"mode": "per-char", "locale": "en", "char_width_factor": 0.6},
  "reduced_motion": {"mode": "static", "visible": ["project-card"]}
}
```

- At most `3` scenes; each scene id is the animated SVG element id and each interval stays inside the duration.
- Default duration `8 s`, hard cap `12 s`; the size budget is `2 MB` (`max_size_mb: 2.0`).
- **Interpolation contract**: smooth scene motion must use `linear` interpolation (CSS default / SMIL `calcMode="linear"`). `discrete` stepping is allowed only for the typewriter reveal.
- **Typewriter contract**: the locale decides the writing system. Latin locales (`en`, `fr`, `de`) reveal per character at `0.6 × font-size` per glyph; CJK locales (`zh-Hans`, `zh-Hant`, `ja`, `ko`) reveal per word or per line at `1.0 × font-size`. `char_width_factor` must match the locale table.
- The static reduced-motion state exposes every scene id, byte-sorted.

### Budget degradation and fallback

When an 8-second default would exceed the `2 MB` budget, the renderer automatically re-renders at a reduced duration (stepping down to a `5 s` floor) and then reduced FPS (down to `15`), rescaling scene intervals with the duration. The final effective `duration`/`fps` are written back to the motion JSON (`--motion-json`) so the recorded parameters match the derived GIF. If the budget floor is still exceeded, the renderer falls back to a single static frame, which is always acceptable.

## Render

The bundled renderer requires Python with Pillow, `ffmpeg`, and either `rsvg-convert` or macOS `sips`:

```bash
python3 scripts/render_motion_gif.py \
  assets/readme/hero.svg \
  assets/readme/hero.gif \
  --spec assets/readme/hero-motion.json
```

Both `--spec` and `--timeline` accept v1 and v2 inputs. Pass `--motion-json PATH` to write the final effective spec (including any budget-degraded `duration`/`fps`) next to the GIF.

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

## Demo Recording

Runtime-captured demos are an opt-in variant of motion: the terminal output
of an archived demo script becomes a `.cast` recording and then a deterministic
GIF, declared as a role-`demo` asset. Use `skill/scripts/record_demo.py`.

### Pipeline and dependencies

```text
demo/<name>.sh|.txt  --asciinema rec-->  assets/readme-showcase/<locale>/<name>.cast
assets/.../<name>.cast  --agg-->  assets/readme-showcase/<locale>/<name>.gif
```

- `asciinema` records the script run into a `.cast` (wall-clock timestamps,
  idle limited to 2 s); `agg` renders the cast into the GIF. agg embeds its own
  GIF encoder, so **no ffmpeg step is needed** on this route.
- Both tools are opt-in: they are **never auto-installed**. When either is
  missing, `record_demo` fails with a clear `DemoDependencyError` install hint
  and the static SVG route stays the default.
- The script must be archived under `demo/` with `.sh`, `.txt`, or `.cast`
  suffix; outputs must be role-demo assets under
  `assets/readme-showcase/<locale>/` (the Task 4.1 runtime-captured contract:
  `captured: true`, `demo_script_ref` bound to the archived script, provenance
  `kind: derived`). A `.cast` input is used directly and never executed.

```bash
python3 scripts/record_demo.py \
  demo/demo.sh \
  assets/readme-showcase/en/demo.cast \
  assets/readme-showcase/en/demo.gif \
  --verify
```

`--verify` re-renders the same cast twice and compares SHA-256. The demo
approval envelope (Task 4.3) hooks into the execution seam: `record_demo`
accepts an `approval_check(script)` callback that runs before any script
execution and aborts the recording by raising. `.cast` inputs never execute
and never trigger the check.

### Determinism

The deterministic claim is cast → GIF: **one cast rendered twice by agg
produces byte-identical output**. Empirically verified 2026-08-13 on
`agg 1.9.0` with the fixed option set (`--theme asciinema
--idle-time-limit 2 --last-frame-duration 2 --fps-cap 20 --speed 1`): a
real demo cast rendered three times — twice through `record_demo.py
--verify` and once through an independent direct `agg` invocation — produced
identical bytes every time
(`render sha256: eedf8226a21e94005ed29ebd050e5f52ea5d081f3c7839655baab3c6d6b3d8df`).
Recording itself is not reproducible across runs (asciinema records real
timestamps), so the cast is the archived, hash-bound source: change the
script, re-record, and re-declare the manifest.

If `--verify` ever reports `identical=False`, treat the render as
nondeterministic and fall back to static SVG per the route fallback contract.

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
