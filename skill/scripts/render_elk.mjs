#!/usr/bin/env node

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, mkdtemp, open, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ELK_VERSION = "0.9.3";
const NODE_VERSION = "22.22.3";
const PACKAGE_INTEGRITY = "sha512-f/ZeWvW/BCXbhGEf1Ujp29EASo/lk1FDnETgNKwJrsVvGZhUWCZyg3xLJjAsxfOmt8KjswHmI5EwCQcPMpOYhQ==";
const PACKAGE_SHA256 = "fb9bb80b980c72022fb4540b38aa0545242b4eb67b82250aeae2f0beb67eea25";
const MODULE_SHA256 = "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4";
const LICENSE_SHA256 = "89591d4578fb1ebd91501312a3d25f021bd865a2e436641c1cf7b1bc7e3c1617";
const LICENSE = "EPL-2.0";
const TIMEOUT_MS = 30_000;
const MAX_INPUT_BYTES = 256 * 1024;
const MAX_ENGINE_BYTES = 2 * 1024 * 1024;
const MAX_METADATA_BYTES = 64 * 1024;
const MAX_SVG_BYTES = 2 * 1024 * 1024;
const MAX_PROCESS_BYTES = 1024 * 1024;
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const VENDOR_ROOT = resolve(SCRIPT_DIR, "../vendor/elkjs");
const PACKAGE_PATH = join(VENDOR_ROOT, "package.json");
const MODULE_PATH = join(VENDOR_ROOT, "lib/elk.bundled.js");
const LICENSE_PATH = join(VENDOR_ROOT, "LICENSE.md");
const HELP = `Usage:
  node skill/scripts/render_elk.mjs \\
    --input RUN_DIR/diagram.diagram.json \\
    --output RUN_DIR/diagram.svg \\
    --metadata RUN_DIR/diagram.engine.json

Exit codes:
  0  available and validated
  1  unavailable, invalid, timeout, or nondeterministic; existing output preserved
  2  usage, input, vendor identity, or runtime error
`;
const ENVELOPE_FIELDS = new Set([
  "schema_version",
  "diagram_type",
  "accessibility_title",
  "accessibility_claim_id",
  "direction",
  "palette",
  "groups",
  "nodes",
  "edges",
  "claim_ids",
]);
const PALETTE_FIELDS = new Set([
  "background",
  "node_background",
  "node_border",
  "node_text",
  "edge_color",
  "edge_label_color",
]);
const GROUP_FIELDS = new Set(["id", "label", "parent_id", "claim_id"]);
const NODE_FIELDS = new Set(["id", "label", "group_id", "kind", "claim_id"]);
const EDGE_FIELDS = new Set(["source", "target", "label", "claim_id"]);
const ALLOWED_TYPES = new Set(["architecture", "flowchart", "c4"]);
const ALLOWED_DIRECTIONS = new Set(["TB", "BT", "LR", "RL"]);
const ALLOWED_KINDS = new Set([
  "component",
  "service",
  "database",
  "person",
  "system",
  "external",
  "container",
]);
const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;
const OPACITY_VALUE_PATTERN = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?%?$/;

class AdapterError extends Error {
  constructor(code, message, exitCode = 1, status = "invalid") {
    super(message);
    this.code = code;
    this.exitCode = exitCode;
    this.status = status;
  }
}

function fail(code, message, exitCode = 1, status = "invalid") {
  throw new AdapterError(code, message, exitCode, status);
}

function sha256(raw) {
  return createHash("sha256").update(raw).digest("hex");
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function exactObject(value, fields, context) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("E_INPUT_SCHEMA", `${context} must be an object`, 2);
  }
  for (const field of fields) {
    if (!(field in value)) fail("E_INPUT_SCHEMA", `${context}.${field} is required`, 2);
  }
  for (const field of Object.keys(value)) {
    if (!fields.has(field)) fail("E_INPUT_SCHEMA", `${context}.${field} is forbidden`, 2);
  }
  return value;
}

function boundedText(value, context, nullable = false) {
  if (nullable && value === null) return null;
  if (
    typeof value !== "string"
    || value.length < 1
    || value.length > 120
    || value.includes("\n")
    || value.includes("\r")
  ) {
    fail("E_INPUT_SCHEMA", `${context} must be single-line text within 120 characters`, 2);
  }
  return value;
}

function identifier(value, context, nullable = false) {
  if (nullable && value === null) return null;
  if (typeof value !== "string" || !ID_PATTERN.test(value)) {
    fail("E_INPUT_SCHEMA", `${context} must be a bounded identifier`, 2);
  }
  return value;
}

function validateEnvelope(value) {
  const input = exactObject(value, ENVELOPE_FIELDS, "input");
  if (input.schema_version !== 1) fail("E_INPUT_SCHEMA", "input.schema_version must be 1", 2);
  if (!ALLOWED_TYPES.has(input.diagram_type)) fail("E_INPUT_SCHEMA", "input.diagram_type is not allowed", 2);
  if (!ALLOWED_DIRECTIONS.has(input.direction)) fail("E_INPUT_SCHEMA", "input.direction is not allowed", 2);
  boundedText(input.accessibility_title, "input.accessibility_title");
  identifier(input.accessibility_claim_id, "input.accessibility_claim_id");

  const palette = exactObject(input.palette, PALETTE_FIELDS, "input.palette");
  for (const field of PALETTE_FIELDS) {
    if (typeof palette[field] !== "string" || !COLOR_PATTERN.test(palette[field])) {
      fail("E_INPUT_SCHEMA", `input.palette.${field} must be a six-digit hex color`, 2);
    }
  }
  if (!Array.isArray(input.groups) || input.groups.length > 50) {
    fail("E_INPUT_SCHEMA", "input.groups must contain at most 50 groups", 2);
  }
  if (!Array.isArray(input.nodes) || input.nodes.length < 1 || input.nodes.length > 100) {
    fail("E_INPUT_SCHEMA", "input.nodes must contain 1-100 nodes", 2);
  }
  if (!Array.isArray(input.edges) || input.edges.length > 200) {
    fail("E_INPUT_SCHEMA", "input.edges must contain at most 200 edges", 2);
  }

  const groups = input.groups.map((raw, index) => {
    const item = exactObject(raw, GROUP_FIELDS, `input.groups[${index}]`);
    return {
      id: identifier(item.id, `input.groups[${index}].id`),
      label: boundedText(item.label, `input.groups[${index}].label`),
      parent_id: identifier(item.parent_id, `input.groups[${index}].parent_id`, true),
      claim_id: identifier(item.claim_id, `input.groups[${index}].claim_id`),
    };
  });
  const nodes = input.nodes.map((raw, index) => {
    const item = exactObject(raw, NODE_FIELDS, `input.nodes[${index}]`);
    if (!ALLOWED_KINDS.has(item.kind)) {
      fail("E_INPUT_SCHEMA", `input.nodes[${index}].kind is not allowed`, 2);
    }
    return {
      id: identifier(item.id, `input.nodes[${index}].id`),
      label: boundedText(item.label, `input.nodes[${index}].label`),
      group_id: identifier(item.group_id, `input.nodes[${index}].group_id`, true),
      kind: item.kind,
      claim_id: identifier(item.claim_id, `input.nodes[${index}].claim_id`),
    };
  });
  const edges = input.edges.map((raw, index) => {
    const item = exactObject(raw, EDGE_FIELDS, `input.edges[${index}]`);
    const label = boundedText(item.label, `input.edges[${index}].label`, true);
    const claimId = identifier(item.claim_id, `input.edges[${index}].claim_id`, true);
    if ((label === null) !== (claimId === null)) {
      fail("E_INPUT_SCHEMA", `input.edges[${index}] label and claim_id must both be null or text`, 2);
    }
    return {
      source: identifier(item.source, `input.edges[${index}].source`),
      target: identifier(item.target, `input.edges[${index}].target`),
      label,
      claim_id: claimId,
    };
  });

  const allIds = [...groups.map((item) => item.id), ...nodes.map((item) => item.id)];
  if (new Set(allIds).size !== allIds.length) fail("E_INPUT_SCHEMA", "group and node ids must be unique", 2);
  const groupIds = new Set(groups.map((item) => item.id));
  const nodeIds = new Set(nodes.map((item) => item.id));
  const groupById = new Map(groups.map((item) => [item.id, item]));
  for (const group of groups) {
    if (group.parent_id !== null && !groupIds.has(group.parent_id)) {
      fail("E_INPUT_SCHEMA", `group ${group.id} references an unknown parent`, 2);
    }
    const seen = new Set([group.id]);
    let cursor = group.parent_id;
    while (cursor !== null) {
      if (seen.has(cursor)) fail("E_INPUT_SCHEMA", "group hierarchy contains a cycle", 2);
      seen.add(cursor);
      cursor = groupById.get(cursor)?.parent_id ?? null;
    }
  }
  for (const node of nodes) {
    if (node.group_id !== null && !groupIds.has(node.group_id)) {
      fail("E_INPUT_SCHEMA", `node ${node.id} references an unknown group`, 2);
    }
  }
  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      fail("E_INPUT_SCHEMA", "edge references an unknown node", 2);
    }
  }
  if (
    !Array.isArray(input.claim_ids)
    || input.claim_ids.some((item, index) => identifier(item, `input.claim_ids[${index}]`) !== item)
    || canonical(input.claim_ids) !== canonical([...new Set(input.claim_ids)].sort())
  ) {
    fail("E_INPUT_SCHEMA", "input.claim_ids must be a sorted unique identifier list", 2);
  }
  const usedClaims = [
    input.accessibility_claim_id,
    ...groups.map((item) => item.claim_id),
    ...nodes.map((item) => item.claim_id),
    ...edges.flatMap((item) => item.claim_id === null ? [] : [item.claim_id]),
  ].sort();
  if (canonical(input.claim_ids) !== canonical(usedClaims)) {
    fail("E_INPUT_SCHEMA", "input.claim_ids must exactly match semantic claims", 2);
  }
  return { ...input, groups, nodes, edges };
}

async function readBounded(path, maximum, code, exitCode = 2) {
  let expected;
  try {
    expected = await lstat(path, { bigint: true });
  } catch {
    fail(code, `${basename(path)} is unavailable`, exitCode, exitCode === 1 ? "unavailable" : "invalid");
  }
  if (expected.isSymbolicLink() || !expected.isFile() || expected.size > BigInt(maximum)) {
    fail(code, `${basename(path)} exceeds contract`, exitCode);
  }
  let handle;
  try {
    handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_NONBLOCK ?? 0));
    const opened = await handle.stat({ bigint: true });
    if (
      !opened.isFile()
      || opened.dev !== expected.dev
      || opened.ino !== expected.ino
      || opened.size !== expected.size
      || opened.mtimeNs !== expected.mtimeNs
    ) {
      fail(code, `${basename(path)} changed before read`, exitCode);
    }
    const chunks = [];
    let total = 0;
    while (total <= maximum) {
      const chunk = Buffer.alloc(Math.min(64 * 1024, maximum + 1 - total));
      const { bytesRead } = await handle.read(chunk, 0, chunk.length, total);
      if (bytesRead === 0) break;
      chunks.push(chunk.subarray(0, bytesRead));
      total += bytesRead;
    }
    const after = await handle.stat({ bigint: true });
    if (
      total > maximum
      || BigInt(total) !== opened.size
      || after.dev !== opened.dev
      || after.ino !== opened.ino
      || after.size !== opened.size
      || after.mtimeNs !== opened.mtimeNs
    ) {
      fail(code, `${basename(path)} changed during read`, exitCode);
    }
    return Buffer.concat(chunks);
  } catch (error) {
    if (error instanceof AdapterError) throw error;
    fail(code, `${basename(path)} cannot be read`, exitCode);
  } finally {
    if (handle) await handle.close();
  }
}

async function parseJson(path, maximum, code, exitCode = 2) {
  const raw = await readBounded(path, maximum, code, exitCode);
  try {
    return { raw, value: JSON.parse(raw.toString("utf8")) };
  } catch {
    fail(code, `${basename(path)} is not valid UTF-8 JSON`, exitCode);
  }
}

async function verifyEngine() {
  if (process.versions.node !== NODE_VERSION) {
    fail("E_ENGINE_RUNTIME", `ELK adapter requires Node ${NODE_VERSION}`, 2);
  }
  const [{ raw: packageRaw, value: packageValue }, moduleRaw, licenseRaw] = await Promise.all([
    parseJson(PACKAGE_PATH, MAX_METADATA_BYTES, "E_ENGINE_IDENTITY", 1),
    readBounded(MODULE_PATH, MAX_ENGINE_BYTES, "E_ENGINE_IDENTITY", 1),
    readBounded(LICENSE_PATH, MAX_METADATA_BYTES, "E_ENGINE_LICENSE", 1),
  ]);
  if (
    packageValue.name !== "elkjs"
    || packageValue.version !== ELK_VERSION
    || packageValue.license !== LICENSE
    || packageValue.main !== "lib/main"
  ) {
    fail("E_ENGINE_IDENTITY", "vendored ELK package identity mismatch", 2);
  }
  if (sha256(packageRaw) !== PACKAGE_SHA256 || sha256(moduleRaw) !== MODULE_SHA256) {
    fail("E_ENGINE_IDENTITY", "vendored ELK package digest mismatch", 2);
  }
  if (sha256(licenseRaw) !== LICENSE_SHA256) {
    fail("E_ENGINE_LICENSE", "vendored ELK license digest mismatch", 2);
  }
  return { packageRaw, moduleRaw, licenseRaw };
}

function wrapLabel(label) {
  const characters = [...label];
  if (characters.length <= 22) return [label];
  const words = label.includes(" ") ? label.split(/\s+/) : characters;
  const lines = [];
  let line = "";
  for (const word of words) {
    const separator = label.includes(" ") && line ? " " : "";
    if ([...(line + separator + word)].length > 22 && line) {
      lines.push(line);
      line = word;
    } else {
      line += separator + word;
    }
  }
  if (line) lines.push(line);
  return lines;
}

function textWidth(value) {
  return [...value].reduce((width, character) => width + (/\p{Script=Han}|\p{Script=Hiragana}|\p{Script=Katakana}/u.test(character) ? 14 : character === " " ? 4 : 8), 0);
}

function graphInput(input) {
  const groups = new Map(input.groups.map((item) => [item.id, {
    id: item.id,
    layoutOptions: {
      "elk.padding": "[top=46,left=24,bottom=24,right=24]",
      "elk.spacing.nodeNode": "56",
      "elk.layered.spacing.nodeNodeBetweenLayers": "96",
    },
    children: [],
  }]));
  const rootChildren = [];
  for (const item of input.groups) {
    (item.parent_id === null ? rootChildren : groups.get(item.parent_id).children).push(groups.get(item.id));
  }
  for (const item of input.nodes) {
    const lines = wrapLabel(item.label);
    const width = Math.max(140, Math.min(300, Math.max(...lines.map(textWidth)) + 48));
    const child = { id: item.id, width, height: Math.max(70, 36 + lines.length * 20) };
    (item.group_id === null ? rootChildren : groups.get(item.group_id).children).push(child);
  }
  return {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": { LR: "RIGHT", RL: "LEFT", TB: "DOWN", BT: "UP" }[input.direction],
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.padding": "[top=24,left=24,bottom=24,right=24]",
      "elk.spacing.nodeNode": "72",
      "elk.layered.spacing.nodeNodeBetweenLayers": "132",
      "elk.spacing.edgeLabel": "12",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
    },
    children: rootChildren,
    edges: input.edges.map((item, index) => ({
      id: `edge-${index}`,
      sources: [item.source],
      targets: [item.target],
      ...(item.label === null ? {} : {
        labels: [{ id: `edge-label-${index}`, text: item.label, width: textWidth(item.label) + 20, height: 24 }],
      }),
    })),
  };
}

function xml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function svgId(prefix, value) {
  const raw = `${prefix}-${value}`;
  return raw.length <= 64 ? raw : `${prefix}-${sha256(Buffer.from(value)).slice(0, 40)}`;
}

function number(value) {
  const rounded = Math.round(Number(value) * 100) / 100;
  if (!Number.isFinite(rounded)) fail("E_ENGINE_RENDER", "ELK returned invalid geometry");
  return String(Object.is(rounded, -0) ? 0 : rounded);
}

function textElement(label, x, y, color, id) {
  const lines = wrapLabel(label);
  const start = y - ((lines.length - 1) * 10);
  return `<text id="${id}" x="${number(x)}" y="${number(start)}" fill="${color}" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" font-size="14" font-weight="600" text-anchor="middle">\n${lines.map((line, index) => `<tspan x="${number(x)}" dy="${index === 0 ? 0 : 20}">${xml(line)}</tspan>`).join("\n")}\n</text>`;
}

function renderSvg(layout, input) {
  if (!Number.isFinite(layout.width) || !Number.isFinite(layout.height) || layout.width <= 0 || layout.height <= 0) {
    fail("E_ENGINE_RENDER", "ELK returned invalid canvas geometry");
  }
  const groupIds = new Set(input.groups.map((item) => item.id));
  const groupById = new Map(input.groups.map((item) => [item.id, item]));
  const nodeById = new Map(input.nodes.map((item) => [item.id, item]));
  const positions = new Map([["root", { x: 0, y: 0 }]]);
  const groups = [];
  const nodes = [];
  function walk(children, parentX, parentY) {
    for (const child of children ?? []) {
      const x = parentX + (child.x ?? 0);
      const y = parentY + (child.y ?? 0);
      positions.set(child.id, { x, y });
      const target = groupIds.has(child.id) ? groups : nodes;
      target.push({ ...child, x, y });
      walk(child.children, x, y);
    }
  }
  walk(layout.children, 0, 0);
  const palette = input.palette;
  const width = Math.ceil(layout.width);
  const height = Math.ceil(layout.height);
  const output = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">`,
    `<title>${xml(input.accessibility_title)}</title>`,
    "<defs>",
    `<filter id="elk-shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="${palette.node_border}" flood-opacity="0.14"/></filter>`,
    `<marker id="elk-arrow" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0 0 L10 4 L0 8 Z" fill="${palette.edge_color}"/></marker>`,
    "</defs>",
    `<rect x="0" y="0" width="${width}" height="${height}" fill="${palette.background}"/>`,
  ];
  for (const group of groups) {
    const semantic = groupById.get(group.id);
    output.push(`<rect id="${svgId("group", group.id)}" x="${number(group.x)}" y="${number(group.y)}" width="${number(group.width)}" height="${number(group.height)}" rx="18" fill="${palette.background}" stroke="${palette.node_border}" stroke-width="1.5" stroke-dasharray="6 5"/>`);
    output.push(textElement(semantic.label, group.x + group.width / 2, group.y + 25, palette.node_text, svgId("group-label", group.id)));
  }
  for (const [index, edge] of (layout.edges ?? []).entries()) {
    const offset = positions.get(edge.container ?? "root") ?? { x: 0, y: 0 };
    const sections = edge.sections ?? [];
    for (const [sectionIndex, section] of sections.entries()) {
      const points = [section.startPoint, ...(section.bendPoints ?? []), section.endPoint];
      const path = points.map((point, pointIndex) => `${pointIndex === 0 ? "M" : "L"}${number(offset.x + point.x)} ${number(offset.y + point.y)}`).join(" ");
      output.push(`<path id="edge-${index}-${sectionIndex}" d="${path}" fill="none" stroke="${palette.edge_color}" stroke-width="2" stroke-linejoin="round" marker-end="url(#elk-arrow)"/>`);
    }
    for (const [labelIndex, label] of (edge.labels ?? []).entries()) {
      const x = offset.x + label.x;
      const y = offset.y + label.y;
      output.push(`<rect id="edge-label-bg-${index}-${labelIndex}" x="${number(x - 5)}" y="${number(y - 2)}" width="${number(label.width + 10)}" height="${number(label.height + 4)}" rx="8" fill="${palette.background}"/>`);
      output.push(textElement(label.text, x + label.width / 2, y + 17, palette.edge_label_color, `edge-label-text-${index}-${labelIndex}`));
    }
  }
  for (const node of nodes) {
    const semantic = nodeById.get(node.id);
    const radius = semantic.kind === "person" ? Math.min(34, node.height / 2) : semantic.kind === "system" ? 8 : 16;
    const dash = semantic.kind === "external" ? " stroke-dasharray=\"7 5\"" : "";
    output.push(`<rect id="${svgId("node", node.id)}" x="${number(node.x)}" y="${number(node.y)}" width="${number(node.width)}" height="${number(node.height)}" rx="${number(radius)}" fill="${palette.node_background}" stroke="${palette.node_border}" stroke-width="1.5"${dash} filter="url(#elk-shadow)"/>`);
    if (semantic.kind === "database") {
      output.push(`<ellipse id="${svgId("database-cap", node.id)}" cx="${number(node.x + node.width / 2)}" cy="${number(node.y + 12)}" rx="${number(node.width / 2 - 1)}" ry="11" fill="${palette.node_background}" stroke="${palette.node_border}" stroke-width="1.5"/>`);
    }
    output.push(textElement(semantic.label, node.x + node.width / 2, node.y + node.height / 2 + 5, palette.node_text, svgId("node-label", node.id)));
  }
  output.push("</svg>", "");
  return Buffer.from(output.join("\n"), "utf8");
}

function decodeXml(value) {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

function opacityHides(value) {
  const normalized = value.trim();
  if (!OPACITY_VALUE_PATTERN.test(normalized)) return true;
  const numeric = Number(normalized.endsWith("%") ? normalized.slice(0, -1) : normalized);
  return !Number.isFinite(numeric) || numeric <= 0;
}

function validateSvg(raw, input, semantic = false) {
  if (!Buffer.isBuffer(raw) || raw.length < 1 || raw.length > MAX_SVG_BYTES) {
    fail("E_SVG_UNSAFE", "SVG exceeds byte contract");
  }
  const svg = raw.toString("utf8");
  if (Buffer.byteLength(svg, "utf8") !== raw.length || svg.includes("\uFFFD")) {
    fail("E_SVG_UNSAFE", "SVG must be valid UTF-8");
  }
  if (
    /<!doctype|<!entity|<\?xml-stylesheet|\bxml:base\s*=/i.test(svg)
    || /<(?:[A-Za-z_][\w.-]*:)?(?:script|style|foreignobject|image|animate|animatecolor|animatemotion|animatetransform|discard|set|mpath)\b/i.test(svg)
    || /\son[a-z]+\s*=|@import/i.test(svg)
    || /\b(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))/i.test(svg)
    || /\b(?:display|visibility)\s*=\s*["'](?:none|hidden|collapse)["']/i.test(svg)
    || /\b(?:clip-path|mask)\s*=/i.test(svg)
    || /\b(?:href|xlink:href)\s*=\s*["'](?!#)[^"']*["']/i.test(svg)
  ) {
    fail("E_SVG_UNSAFE", "SVG contains unsafe content");
  }
  for (const match of svg.matchAll(/\b(?:opacity|fill-opacity)\s*=\s*["']([^"']*)["']/gi)) {
    if (opacityHides(match[1])) fail("E_SVG_UNSAFE", "SVG contains hidden semantic content");
  }
  const ids = [...svg.matchAll(/\bid\s*=\s*["']([^"']+)["']/gi)].map((match) => match[1]);
  if (ids.some((id) => !ID_PATTERN.test(id)) || new Set(ids).size !== ids.length) {
    fail("E_SVG_UNSAFE", "SVG ids must be unique bounded identifiers");
  }
  const idSet = new Set(ids);
  for (const match of svg.matchAll(/url\s*\(\s*([^)]*?)\s*\)/gi)) {
    const reference = match[1].match(/^["']?#([A-Za-z0-9][A-Za-z0-9_-]{0,63})["']?$/);
    if (reference === null || !idSet.has(reference[1])) {
      fail("E_SVG_UNSAFE", "SVG URL must reference a defined local id");
    }
  }
  const openTag = svg.match(/<svg\b([^>]*)>/i);
  if (
    !openTag
    || !/\bwidth\s*=\s*["'](?:[1-9]\d*)(?:\.\d+)?["']/i.test(openTag[1])
    || !/\bheight\s*=\s*["'](?:[1-9]\d*)(?:\.\d+)?["']/i.test(openTag[1])
    || !/\bviewBox\s*=\s*["']\s*-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+[1-9]\d*(?:\.\d+)?\s+[1-9]\d*(?:\.\d+)?\s*["']/i.test(openTag[1])
    || !/\brole\s*=\s*["']img["']/i.test(openTag[1])
  ) {
    fail("E_SVG_UNSAFE", "SVG root contract is invalid");
  }
  const titles = [...svg.matchAll(/<title\b[^>]*>([\s\S]*?)<\/title>/gi)];
  if (titles.length !== 1 || decodeXml(titles[0][1].trim()) !== input.accessibility_title) {
    fail("E_SVG_UNSAFE", "SVG title does not match accessibility contract");
  }
  if (semantic) {
    const actual = [...svg.matchAll(/<text\b[^>]*>([\s\S]*?)<\/text>/gi)]
      .map((match) => decodeXml(match[1].replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim()));
    const expected = [
      ...input.groups.map((item) => item.label),
      ...input.nodes.map((item) => item.label),
      ...input.edges.flatMap((item) => item.label === null ? [] : [item.label]),
    ];
    if (canonical(actual.sort()) !== canonical(expected.sort())) {
      fail("E_SVG_LABELS", "SVG labels do not exactly match semantic input");
    }
  }
}

function parseArguments(raw, worker = false) {
  const allowed = new Set(["--input", "--output", "--metadata", ...(worker ? ["--worker-output"] : [])]);
  const values = {};
  for (let index = 0; index < raw.length; index += 2) {
    const key = raw[index];
    const value = raw[index + 1];
    if (!allowed.has(key) || value === undefined || value.startsWith("--") || key in values) {
      fail("E_USAGE", "invalid ELK adapter arguments", 2);
    }
    values[key] = value;
  }
  for (const key of allowed) {
    if (!(key in values)) fail("E_USAGE", `${key} is required`, 2);
  }
  return values;
}

async function atomicWrite(path, raw) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}-${Date.now()}`;
  try {
    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(raw);
      await handle.sync();
    } finally {
      await handle.close();
    }
    await rename(temporary, path);
  } finally {
    await rm(temporary, { force: true });
  }
}

async function readExisting(path, maximum) {
  try {
    return await readBounded(path, maximum, "E_ATOMIC_WRITE");
  } catch (error) {
    if (error instanceof AdapterError && error.message.endsWith(" is unavailable")) return null;
    throw error;
  }
}

async function atomicWritePair(outputPath, outputRaw, metadataPath, metadataRaw) {
  const [previousOutput, previousMetadata] = await Promise.all([
    readExisting(outputPath, MAX_SVG_BYTES),
    readExisting(metadataPath, MAX_METADATA_BYTES),
  ]);
  let outputWritten = false;
  try {
    await atomicWrite(outputPath, outputRaw);
    outputWritten = true;
    await atomicWrite(metadataPath, metadataRaw);
  } catch (error) {
    if (outputWritten) {
      try {
        if (previousOutput === null) await rm(outputPath, { force: true });
        else await atomicWrite(outputPath, previousOutput);
        if (previousMetadata !== null) await atomicWrite(metadataPath, previousMetadata);
      } catch {
        fail("E_ATOMIC_ROLLBACK", "failed to restore last-known-good files");
      }
    }
    throw error;
  }
}

async function runWorker(args) {
  const inputPath = resolve(args["--input"]);
  const workerOutput = resolve(args["--worker-output"]);
  const [{ value: rawInput }, engine] = await Promise.all([
    parseJson(inputPath, MAX_INPUT_BYTES, "E_INPUT_SCHEMA"),
    verifyEngine(),
  ]);
  const input = validateEnvelope(rawInput);
  const vendorSnapshot = await mkdtemp(join(tmpdir(), "readme-showcase-elk-vendor-"));
  try {
    const snapshotModule = join(vendorSnapshot, "elk.cjs");
    await writeFile(snapshotModule, engine.moduleRaw, { flag: "wx", mode: 0o600 });
    let imported;
    try {
      imported = await import(pathToFileURL(snapshotModule).href);
    } catch {
      fail("E_ENGINE_IMPORT", "vendored ELK import failed");
    }
    const ELK = imported.default;
    if (typeof ELK !== "function") fail("E_ENGINE_IMPORT", "vendored ELK export is unavailable");
    let layout;
    try {
      layout = await new ELK().layout(graphInput(input));
    } catch (error) {
      fail("E_ENGINE_RENDER", `ELK layout failed: ${error instanceof Error ? error.message : "unknown error"}`);
    }
    const rawSvg = renderSvg(layout, input);
    validateSvg(rawSvg, input, true);
    await atomicWrite(workerOutput, rawSvg);
  } finally {
    await rm(vendorSnapshot, { recursive: true, force: true });
  }
}

async function runIsolatedWorker(args, outputPath) {
  const script = resolve(process.argv[1]);
  const work = await mkdtemp(join(tmpdir(), "readme-showcase-elk-"));
  const childArgs = [
    script,
    "--worker",
    "--input", args["--input"],
    "--output", args["--output"],
    "--metadata", args["--metadata"],
    "--worker-output", outputPath,
  ];
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(process.execPath, childArgs, {
      cwd: work,
      detached: process.platform !== "win32",
      env: { PATH: process.env.PATH ?? "", LC_ALL: "C", TZ: "UTC" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stderr = [];
    let outputBytes = 0;
    let settled = false;
    const finish = async (error, code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      await rm(work, { recursive: true, force: true });
      if (error) rejectPromise(error);
      else resolvePromise({ code, stderr: Buffer.concat(stderr).toString("utf8") });
    };
    const stop = (error) => {
      if (process.platform !== "win32") {
        try { process.kill(-child.pid, "SIGKILL"); } catch {}
      } else child.kill("SIGKILL");
      finish(error);
    };
    child.stdout.on("data", (chunk) => {
      outputBytes += chunk.length;
      if (outputBytes > MAX_PROCESS_BYTES) stop(new AdapterError("E_ENGINE_OUTPUT_LIMIT", "ELK worker output exceeded limit"));
    });
    child.stderr.on("data", (chunk) => {
      outputBytes += chunk.length;
      if (outputBytes > MAX_PROCESS_BYTES) stop(new AdapterError("E_ENGINE_OUTPUT_LIMIT", "ELK worker output exceeded limit"));
      else stderr.push(chunk);
    });
    child.on("error", (error) => finish(new AdapterError("E_ENGINE_PROCESS", `ELK worker failed: ${error.message}`)));
    child.on("close", (code) => finish(null, code ?? 1));
    const timer = setTimeout(() => stop(new AdapterError("E_ENGINE_TIMEOUT", "ELK worker exceeded 30 seconds", 1, "timeout")), TIMEOUT_MS);
  });
}

function workerFailure(result) {
  const match = result.stderr.match(/^([A-Z][A-Z0-9_]+): ([^\r\n]*)/m);
  return match ? new AdapterError(match[1], match[2]) : new AdapterError("E_ENGINE_RENDER", "ELK worker failed");
}

async function runController(args) {
  const inputPath = resolve(args["--input"]);
  const outputPath = resolve(args["--output"]);
  const metadataPath = resolve(args["--metadata"]);
  if (
    dirname(inputPath) !== dirname(outputPath)
    || dirname(outputPath) !== dirname(metadataPath)
    || new Set([inputPath, outputPath, metadataPath]).size !== 3
  ) {
    fail("E_USAGE", "input, output, and metadata must be distinct files in one directory", 2);
  }
  const [{ raw: inputRaw, value: rawInput }] = await Promise.all([
    parseJson(inputPath, MAX_INPUT_BYTES, "E_INPUT_SCHEMA"),
    verifyEngine(),
  ]);
  const input = validateEnvelope(rawInput);
  const work = await mkdtemp(join(tmpdir(), "readme-showcase-elk-controller-"));
  try {
    const snapshotInput = join(work, "input.json");
    await writeFile(snapshotInput, inputRaw, { flag: "wx", mode: 0o600 });
    const snapshotArgs = { ...args, "--input": snapshotInput };
    const firstPath = join(work, "first.svg");
    const secondPath = join(work, "second.svg");
    const first = await runIsolatedWorker(snapshotArgs, firstPath);
    if (first.code !== 0) throw workerFailure(first);
    const second = await runIsolatedWorker(snapshotArgs, secondPath);
    if (second.code !== 0) throw workerFailure(second);
    const [firstRaw, secondRaw] = await Promise.all([
      readBounded(firstPath, MAX_SVG_BYTES, "E_SVG_UNSAFE"),
      readBounded(secondPath, MAX_SVG_BYTES, "E_SVG_UNSAFE"),
    ]);
    const runHashes = [sha256(firstRaw), sha256(secondRaw)];
    if (!firstRaw.equals(secondRaw)) {
      fail("E_ENGINE_NONDETERMINISTIC", "ELK output differs across fresh runs", 1, "nondeterministic");
    }
    validateSvg(firstRaw, input, true);
    const rendererRaw = await readBounded(fileURLToPath(import.meta.url), MAX_ENGINE_BYTES, "E_ENGINE_IDENTITY");
    const metadata = {
      schema_version: 1,
      engine_kind: "elk",
      package_name: "elkjs",
      package_version: ELK_VERSION,
      package_integrity: PACKAGE_INTEGRITY,
      package_sha256: PACKAGE_SHA256,
      module_sha256: MODULE_SHA256,
      license_spdx: LICENSE,
      license_sha256: LICENSE_SHA256,
      node_version: process.versions.node,
      platform: process.platform,
      architecture: process.arch,
      input_sha256: sha256(inputRaw),
      renderer_sha256: sha256(rendererRaw),
      output_sha256: runHashes[0],
      run_hashes: runHashes,
      validation: "pass",
      fallback_state: "preserved",
    };
    const metadataRaw = Buffer.from(`${canonical(metadata)}\n`, "utf8");
    await atomicWritePair(outputPath, firstRaw, metadataPath, metadataRaw);
    process.stdout.write(`${JSON.stringify({
      schema_version: 1,
      status: "available",
      output_sha256: runHashes[0],
      metadata_sha256: sha256(metadataRaw),
    })}\n`);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

async function main() {
  try {
    if (process.argv.length === 3 && ["--help", "-h"].includes(process.argv[2])) {
      process.stdout.write(HELP);
      return;
    }
    if (process.argv[2] === "--worker") {
      await runWorker(parseArguments(process.argv.slice(3), true));
      return;
    }
    await runController(parseArguments(process.argv.slice(2), false));
  } catch (error) {
    const known = error instanceof AdapterError ? error : new AdapterError("E_INTERNAL", "unexpected adapter failure");
    process.stdout.write(`${JSON.stringify({ schema_version: 1, status: known.status })}\n`);
    process.stderr.write(`${known.code}: ${known.message}\n`);
    process.exitCode = known.exitCode;
  }
}

await main();
