# Glyphic structure adapter

Glyphic is optional structure infrastructure. `readme-showcase` remains product owner; `beautify-github-readme` remains editorial and visual-rule source.

## Ownership boundary

| Concern | Owner | Glyphic status |
|---|---|---|
| Repository evidence, retrieval, claims, README copy, evaluation, fallback, publish authority | `readme-showcase` | Forbidden |
| Story, title, title bar, palette, art direction, composition, visual QA | `beautify-github-readme` rules adapted by `readme-showcase` | Forbidden |
| `architecture`, `flowchart`, or `c4` groups/nodes/edges layout, edge routing, label wrapping, raw SVG bytes | User-supplied verified Glyphic install | Allowed |

Excluded: required dependency, vendoring, `node_modules`, MCP/API/network access, PNG, ReactFlow, Canvas, Gantt, remote fonts/icons/images, coordinates, arbitrary metadata, README hero/title/palette/copy/claims/composition, SVG post-editing, remote writes.

## Invocation

Node 22 required. Caller supplies isolated `@glyphicjs/core` module root and immutable lock:

```sh
node skill/scripts/render_glyphic.mjs \
  --module-root /absolute/install/node_modules/@glyphicjs/core \
  --engine-lock /absolute/glyphic-engine-lock.json \
  --input run/diagram.glyphic.json \
  --output run/diagram.svg \
  --metadata run/diagram.engine.json
```

Input, output, metadata share one directory. Adapter imports only pinned `processSVG`; it runs two fresh subprocesses with credential-free environment, temporary working directories, 30-second limit, 1 MiB process-output limits, and 2 MiB SVG limit. Repository carries no Glyphic package or lockfile.

## Verified engine lock

Lock binds:

- `@glyphicjs/core@1.3.1`
- `@glyphicjs/schema@1.1.1`
- source commit `ed79edb1624e2de78041611971a963efaea5e080`
- npm SRI, package JSON hash, complete isolated `node_modules` tree hash
- `FSL-1.1-ALv2` license file and hash
- exact Node 22 runtime version

FSL terms apply to pinned package. Adapter does not relabel current package as Apache-2.0 or MIT.

Build the lock outside `node_modules` after installing exact packages in the
same OS/architecture/runtime image that will render:

```sh
README_SHOWCASE_SKILL="${CODEX_HOME:-$HOME/.codex}/skills/readme-showcase"
GLYPHIC_NODE_VERSION="$(node -p 'process.versions.node')"
GLYPHIC_TREE_SHA256="<trusted lowercase SHA-256 from reviewed CI build>"
python3 "$README_SHOWCASE_SKILL/scripts/build_glyphic_engine_lock.py" \
  --install-root /absolute/install \
  --npm-sri "sha512-+wWBhFXOkgS6ZtGk4cHPooIueXt01g3meuHHcZnapBtgPW8IXy8nDFPO1lZXeETVK+NZ6BeCu+blmD3QGr5hDw==" \
  --node-version "$GLYPHIC_NODE_VERSION" \
  --expected-tree-sha256 "$GLYPHIC_TREE_SHA256" \
  --output /absolute/install/glyphic-engine-lock.json
```

`--expected-tree-sha256` is an independent trust input, not a digest copied
from the tree being locked. CI derives and reviews it in a separate repeatable
build, then pins it with the immutable image, exact transitive package list,
Node patch version, architecture, and package SRI. The builder rejects
symlinks, special files, excessive depth/count/bytes, missing directory
entries, or any tree mismatch.

Native optional packages make a lock platform-specific. Never install on macOS
and reuse that tree in Linux, or switch glibc/musl images after locking.

## Semantic envelope

Envelope is strict JSON: schema version, allowed diagram type, accessibility title/claim, direction, six-color palette, groups, nodes, edges, and exact sorted claim IDs. Unknown fields fail. Coordinates, icons, fonts, URLs, resources, and free-form metadata fail.

`readme-showcase` projects the envelope into fixed Glyphic fields and supplies
no font name or font URL. Glyphic emits its fixed unbundled
`Inter, system-ui, sans-serif` fallback stack without a remote `@import`;
audit accepts only that exact engine stack or system-only stacks. Output remains
exact raw bytes. Adapter never sanitizes or repairs output.

## Reject-only gate and fallback

Each render must:

1. Match verified install tree and runtime.
2. Complete twice in fresh processes with identical raw bytes.
3. Produce standalone UTF-8 SVG with positive dimensions, `viewBox`, `role="img"`, exact `<title>`, and exact semantic labels.
4. Contain no scripts, styles, event handlers, animation, foreign objects,
   images, external references, imports, or external resource URLs. Only
   bounded `url(#id)` / `href="#id"` references to unique IDs defined in the
   same SVG are allowed.

Any unavailable engine, identity mismatch, timeout, nondeterminism, unsafe SVG, or schema failure leaves existing SVG and metadata untouched. Caller retains static fallback and decides whether to use it. Adapter never mutates README, last-good assets, GitHub, branches, or pull requests.

This adapter reduces network exposure by providing no network feature and
scrubbing child environment. CI installs inside one immutable Linux image,
then renders the same locked tree with `--network none`, read-only mounts,
dropped capabilities, and no-new-privileges. Universal network isolation still
belongs to caller/CI sandbox. The controller snapshots input and lock bytes
into its private work directory before both render workers start; output is
accepted only when both workers return byte-identical raw SVG.
