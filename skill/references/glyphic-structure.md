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

- `@glyphicjs/core` and `@glyphicjs/schema` version `1.3.1`
- source commit `ed79edb1624e2de78041611971a963efaea5e080`
- npm SRI, package JSON hash, complete isolated `node_modules` tree hash
- `FSL-1.1-ALv2` license file and hash
- exact Node 22 runtime version

FSL terms apply to pinned package. Adapter does not relabel current package as Apache-2.0 or MIT.

## Semantic envelope

Envelope is strict JSON: schema version, allowed diagram type, accessibility title/claim, direction, six-color palette, groups, nodes, edges, and exact sorted claim IDs. Unknown fields fail. Coordinates, icons, fonts, URLs, resources, and free-form metadata fail.

`readme-showcase` projects envelope into fixed Glyphic fields and system font `Arial`. Glyphic output remains exact raw bytes. Adapter never sanitizes or repairs output.

## Reject-only gate and fallback

Each render must:

1. Match verified install tree and runtime.
2. Complete twice in fresh processes with identical raw bytes.
3. Produce standalone UTF-8 SVG with positive dimensions, `viewBox`, `role="img"`, exact `<title>`, and exact semantic labels.
4. Contain no scripts, styles, event handlers, animation, foreign objects, images, external references, imports, or resource URLs.

Any unavailable engine, identity mismatch, timeout, nondeterminism, unsafe SVG, or schema failure leaves existing SVG and metadata untouched. Caller retains static fallback and decides whether to use it. Adapter never mutates README, last-good assets, GitHub, branches, or pull requests.

This adapter reduces network exposure by providing no network feature and scrubbing child environment. Universal network isolation still belongs to caller/CI sandbox.
