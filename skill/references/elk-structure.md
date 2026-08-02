# ELK structure adapter

ELK is optional layout infrastructure. `readme-showcase` owns evidence,
semantics, SVG bytes, evaluation, fallback, and publishing authority.

## Ownership boundary

| Concern | Owner |
|---|---|
| Evidence, claims, README copy, palette, alt/caption, acceptance, fallback, publishing | `readme-showcase` |
| Story, art direction, composition, and visual QA | adapted `beautify-github-readme` rules |
| `architecture`, `flowchart`, or `c4` node/group/edge geometry | vendored `elkjs@0.9.3` |
| Standalone safe SVG serialization | `render_elk.mjs` |

Coordinates, fonts, URLs, resources, arbitrary metadata, remote images, remote
fonts, network access, and README mutations are excluded from the semantic
input. ELK computes geometry only.

## Runtime and invocation

Use exact Node `22.22.3` from the repository or installed Skill `.nvmrc`:

```sh
README_SHOWCASE_SKILL="${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase"
nvm use "$(cat "$README_SHOWCASE_SKILL/.nvmrc")"
node "$README_SHOWCASE_SKILL/scripts/render_elk.mjs" \
  --input run/diagram.diagram.json \
  --output run/diagram.svg \
  --metadata run/diagram.engine.json
```

The installed Skill contains these exact runtime files:

- `vendor/elkjs/lib/elk.bundled.js`
- `vendor/elkjs/package.json`
- `vendor/elkjs/LICENSE.md`

The adapter verifies their pinned SHA-256 hashes before importing ELK. The root
`package-lock.json` binds the source tarball to npm integrity, while the Skill
does not require `node_modules`, Docker, a runtime download, or a network API.

## Semantic envelope

The envelope is strict JSON: schema version, allowed diagram type,
accessibility title/claim, direction, six-color palette, groups, nodes, edges,
and the exact sorted claim IDs. Unknown fields fail. Node kinds are limited to
`component`, `service`, `database`, `person`, `system`, `external`, and
`container`.

## Deterministic reject-only gate

Each render must:

1. Match the pinned ELK package/module/license files and Node patch version.
2. Complete twice in fresh subprocesses with byte-identical SVG.
3. Produce standalone UTF-8 SVG with positive dimensions, `viewBox`,
   `role="img"`, one exact `<title>`, and every semantic label exactly once.
4. Contain no scripts, styles, handlers, animation, foreign objects, images,
   external references, imports, or hidden semantic text. Every local ID
   reference must target an ID defined in the same SVG.
5. Bind input, renderer, engine, output, and both run hashes in adjacent engine
   metadata.

The controller snapshots input in a private temporary directory, gives workers
only `PATH`, `LC_ALL`, and `TZ`, limits process output, and enforces a 30-second
timeout and 2 MiB SVG limit. Any failure leaves existing SVG and metadata
untouched. The caller retains a static SVG fallback.

CI additionally runs the real renderer in a Linux `unshare --net` namespace.
This removes network access without Docker; macOS callers should use their
normal process sandbox when syscall-level network isolation is required.

## License boundary

`elkjs@0.9.3` is distributed under `EPL-2.0`. Its unmodified license is stored
beside the vendored files. Project-authored code remains under the repository's
GPL-3.0-only license.
