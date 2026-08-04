#!/usr/bin/env node

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdtemp, open, readdir, realpath, rename, rm, writeFile } from "node:fs/promises";
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
const MAX_GEOMETRY_BYTES = 2 * 1024 * 1024;
const MAX_PROCESS_BYTES = 1024 * 1024;
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const VENDOR_ROOT = resolve(SCRIPT_DIR, "../vendor/elkjs");
const PACKAGE_PATH = join(VENDOR_ROOT, "package.json");
const MODULE_PATH = join(VENDOR_ROOT, "lib/elk.bundled.js");
const LICENSE_PATH = join(VENDOR_ROOT, "LICENSE.md");
const HELP = `Usage:
  node skill/scripts/render_elk.mjs \\
    --input RUN_DIR/diagram.diagram.json \\
    (--output RUN_DIR/diagram.svg | --geometry RUN_DIR/diagram.geometry.json) \\
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

function geometryCoordinate(value, context) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 20_000) {
    fail("E_OUTPUT_GEOMETRY", `${context} must be a finite non-negative number at most 20000`);
  }
  return value;
}

function geometryInteger(value, context) {
  const checked = geometryCoordinate(value, context);
  const rounded = Math.round(checked);
  if (!Number.isFinite(rounded) || rounded < 0 || rounded > 20_000) {
    fail("E_OUTPUT_GEOMETRY", `${context} is outside the geometry bounds`);
  }
  return Object.is(rounded, -0) ? 0 : rounded;
}

function encodedGeometryInteger(value, context) {
  if (typeof value !== "number" || !Number.isInteger(value) || !Number.isFinite(value) || value < 0 || value > 20_000) {
    fail("E_OUTPUT_GEOMETRY", `${context} must be a bounded integer`);
  }
  return value;
}

function compareIds(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function geometryPoint(point, offsetX, offsetY, context) {
  if (!point || typeof point !== "object" || Array.isArray(point)) {
    fail("E_OUTPUT_GEOMETRY", `${context} is required`);
  }
  const x = geometryCoordinate(point.x, `${context}.x`);
  const y = geometryCoordinate(point.y, `${context}.y`);
  return {
    x: geometryInteger(offsetX + x, `${context}.x`),
    y: geometryInteger(offsetY + y, `${context}.y`),
  };
}

function geometryEngineIdentity(rendererRaw) {
  return {
    engine_kind: "elk",
    package_name: "elkjs",
    package_version: ELK_VERSION,
    package_sha256: PACKAGE_SHA256,
    module_sha256: MODULE_SHA256,
    node_version: process.versions.node,
    renderer_sha256: sha256(rendererRaw),
  };
}

function renderGeometry(layout, input, rendererRaw) {
  if (!layout || typeof layout !== "object" || Array.isArray(layout)) {
    fail("E_OUTPUT_GEOMETRY", "ELK returned no layout");
  }
  const canvas = {
    width: geometryInteger(layout.width, "canvas.width"),
    height: geometryInteger(layout.height, "canvas.height"),
  };
  if (!Array.isArray(layout.children)) fail("E_OUTPUT_GEOMETRY", "layout.children is required");

  const groupById = new Map(input.groups.map((item) => [item.id, item]));
  const nodeById = new Map(input.nodes.map((item) => [item.id, item]));
  const groups = [];
  const nodes = [];
  const ports = [];
  const positions = new Map([["root", { x: 0, y: 0 }]]);
  const seenGroups = new Set();
  const seenNodes = new Set();
  const seenPorts = new Set();

  function walk(children, parentX, parentY, parentId) {
    if (!Array.isArray(children)) fail("E_OUTPUT_GEOMETRY", "layout.children must be an array");
    for (const child of children) {
      if (!child || typeof child !== "object" || Array.isArray(child) || typeof child.id !== "string") {
        fail("E_OUTPUT_GEOMETRY", "laid-out element id is required");
      }
      const localX = geometryCoordinate(child.x, `${child.id}.x`);
      const localY = geometryCoordinate(child.y, `${child.id}.y`);
      const x = parentX + localX;
      const y = parentY + localY;
      const position = { x, y };
      positions.set(child.id, position);
      if (groupById.has(child.id)) {
        const semantic = groupById.get(child.id);
        if (seenGroups.has(child.id) || semantic.parent_id !== parentId) {
          fail("E_OUTPUT_GEOMETRY", `group ${child.id} does not preserve semantic containment`);
        }
        seenGroups.add(child.id);
        groups.push({
          id: semantic.id,
          parent_id: semantic.parent_id,
          x: geometryInteger(x, `${child.id}.x`),
          y: geometryInteger(y, `${child.id}.y`),
          width: geometryInteger(child.width, `${child.id}.width`),
          height: geometryInteger(child.height, `${child.id}.height`),
        });
        walk(child.children ?? [], x, y, child.id);
      } else if (nodeById.has(child.id)) {
        const semantic = nodeById.get(child.id);
        if (seenNodes.has(child.id) || semantic.group_id !== parentId) {
          fail("E_OUTPUT_GEOMETRY", `node ${child.id} does not preserve semantic containment`);
        }
        seenNodes.add(child.id);
        nodes.push({
          id: semantic.id,
          parent_id: semantic.group_id,
          x: geometryInteger(x, `${child.id}.x`),
          y: geometryInteger(y, `${child.id}.y`),
          width: geometryInteger(child.width, `${child.id}.width`),
          height: geometryInteger(child.height, `${child.id}.height`),
        });
        if (child.children !== undefined && (!Array.isArray(child.children) || child.children.length > 0)) {
          fail("E_OUTPUT_GEOMETRY", `node ${child.id} has unexpected children`);
        }
        if (child.ports === undefined) continue;
        if (!Array.isArray(child.ports)) fail("E_OUTPUT_GEOMETRY", `${child.id}.ports must be an array`);
        for (const port of child.ports) {
          if (!port || typeof port !== "object" || Array.isArray(port) || typeof port.id !== "string") {
            fail("E_OUTPUT_GEOMETRY", `${child.id} port id is required`);
          }
          if (seenPorts.has(port.id)) fail("E_OUTPUT_GEOMETRY", `port ${port.id} is duplicated`);
          seenPorts.add(port.id);
          const portX = geometryCoordinate(port.x, `${port.id}.x`);
          const portY = geometryCoordinate(port.y, `${port.id}.y`);
          ports.push({
            id: port.id,
            node_id: semantic.id,
            x: geometryInteger(x + portX, `${port.id}.x`),
            y: geometryInteger(y + portY, `${port.id}.y`),
            width: geometryInteger(port.width, `${port.id}.width`),
            height: geometryInteger(port.height, `${port.id}.height`),
          });
        }
      } else {
        fail("E_OUTPUT_GEOMETRY", `unknown laid-out element ${child.id}`);
      }
    }
  }

  walk(layout.children, 0, 0, null);
  if (seenGroups.size !== input.groups.length || seenNodes.size !== input.nodes.length) {
    fail("E_OUTPUT_GEOMETRY", "ELK omitted a semantic group or node");
  }

  if (!Array.isArray(layout.edges)) fail("E_OUTPUT_GEOMETRY", "layout.edges is required");
  const expectedEdges = input.edges.map((_, index) => `edge-${index}`);
  const edges = [];
  const seenEdges = new Set();
  for (const edge of layout.edges) {
    if (!edge || typeof edge !== "object" || Array.isArray(edge) || typeof edge.id !== "string") {
      fail("E_OUTPUT_GEOMETRY", "laid-out edge id is required");
    }
    if (!expectedEdges.includes(edge.id) || seenEdges.has(edge.id)) {
      fail("E_OUTPUT_GEOMETRY", `edge ${edge.id} is not a semantic edge`);
    }
    seenEdges.add(edge.id);
    if (!Array.isArray(edge.sections) || edge.sections.length === 0) {
      fail("E_OUTPUT_GEOMETRY", `edge ${edge.id} has no sections`);
    }
    const offset = positions.get(edge.container ?? "root");
    if (!offset) fail("E_OUTPUT_GEOMETRY", `edge ${edge.id} has an unknown container`);
    const sections = edge.sections.map((section, index) => {
      if (!section || typeof section !== "object" || Array.isArray(section)) {
        fail("E_OUTPUT_GEOMETRY", `edge ${edge.id} section ${index} is required`);
      }
      if (!Array.isArray(section.bendPoints ?? [])) {
        fail("E_OUTPUT_GEOMETRY", `edge ${edge.id} section ${index}.bendPoints must be an array`);
      }
      return {
        start: geometryPoint(section.startPoint, offset.x, offset.y, `edge ${edge.id} section ${index}.start`),
        bends: (section.bendPoints ?? []).map((point, pointIndex) => geometryPoint(
          point,
          offset.x,
          offset.y,
          `edge ${edge.id} section ${index}.bend${pointIndex}`,
        )),
        end: geometryPoint(section.endPoint, offset.x, offset.y, `edge ${edge.id} section ${index}.end`),
      };
    });
    edges.push({ id: edge.id, sections });
  }
  if (seenEdges.size !== expectedEdges.length) fail("E_OUTPUT_GEOMETRY", "ELK omitted a semantic edge");

  groups.sort((left, right) => compareIds(left.id, right.id));
  nodes.sort((left, right) => compareIds(left.id, right.id));
  ports.sort((left, right) => compareIds(left.id, right.id));
  edges.sort((left, right) => compareIds(left.id, right.id));
  return {
    schema_version: 1,
    engine: geometryEngineIdentity(rendererRaw),
    canvas,
    groups,
    nodes,
    ports,
    edges,
  };
}

const GEOMETRY_FIELDS = new Set(["schema_version", "engine", "canvas", "groups", "nodes", "ports", "edges"]);
const GEOMETRY_ENGINE_FIELDS = new Set([
  "engine_kind",
  "package_name",
  "package_version",
  "package_sha256",
  "module_sha256",
  "node_version",
  "renderer_sha256",
]);
const GEOMETRY_CANVAS_FIELDS = new Set(["width", "height"]);
const GEOMETRY_GROUP_FIELDS = new Set(["id", "parent_id", "x", "y", "width", "height"]);
const GEOMETRY_NODE_FIELDS = new Set(["id", "parent_id", "x", "y", "width", "height"]);
const GEOMETRY_PORT_FIELDS = new Set(["id", "node_id", "x", "y", "width", "height"]);
const GEOMETRY_EDGE_FIELDS = new Set(["id", "sections"]);
const GEOMETRY_SECTION_FIELDS = new Set(["start", "bends", "end"]);
const GEOMETRY_POINT_FIELDS = new Set(["x", "y"]);

function geometryObject(value, fields, context) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("E_OUTPUT_GEOMETRY", `${context} must be an object`);
  }
  if (Object.keys(value).some((field) => !fields.has(field)) || [...fields].some((field) => !(field in value))) {
    fail("E_OUTPUT_GEOMETRY", `${context} has an invalid field set`);
  }
  return value;
}

function validateGeometry(raw, input, expectedRendererSha256) {
  if (!Buffer.isBuffer(raw) || raw.length < 1 || raw.length > MAX_GEOMETRY_BYTES) {
    fail("E_OUTPUT_GEOMETRY", "geometry exceeds byte contract");
  }
  const text = raw.toString("utf8");
  if (Buffer.byteLength(text, "utf8") !== raw.length || text.includes("\uFFFD")) {
    fail("E_OUTPUT_GEOMETRY", "geometry must be valid UTF-8");
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    fail("E_OUTPUT_GEOMETRY", "geometry is not valid JSON");
  }
  if (canonical(value) + "\n" !== text) fail("E_OUTPUT_GEOMETRY", "geometry must be canonical JSON");
  geometryObject(value, GEOMETRY_FIELDS, "geometry");
  if (value.schema_version !== 1) fail("E_OUTPUT_GEOMETRY", "geometry.schema_version must be 1");
  const engine = geometryObject(value.engine, GEOMETRY_ENGINE_FIELDS, "geometry.engine");
  if (
    engine.engine_kind !== "elk"
    || engine.package_name !== "elkjs"
    || engine.package_version !== ELK_VERSION
    || engine.package_sha256 !== PACKAGE_SHA256
    || engine.module_sha256 !== MODULE_SHA256
    || engine.node_version !== NODE_VERSION
    || engine.renderer_sha256 !== expectedRendererSha256
  ) {
    fail("E_OUTPUT_GEOMETRY", "geometry engine identity does not match the pinned adapter");
  }
  const canvas = geometryObject(value.canvas, GEOMETRY_CANVAS_FIELDS, "geometry.canvas");
  encodedGeometryInteger(canvas.width, "geometry.canvas.width");
  encodedGeometryInteger(canvas.height, "geometry.canvas.height");

  const groupIds = new Set(input.groups.map((item) => item.id));
  const nodeIds = new Set(input.nodes.map((item) => item.id));
  const groupParents = new Map(input.groups.map((item) => [item.id, item.parent_id]));
  const nodeParents = new Map(input.nodes.map((item) => [item.id, item.group_id]));
  const checkRect = (item, fields, context, parentField, allowedParents) => {
    geometryObject(item, fields, context);
    if (typeof item.id !== "string") fail("E_OUTPUT_GEOMETRY", `${context}.id is required`);
    if (item[parentField] !== null && !allowedParents.has(item[parentField])) {
      fail("E_OUTPUT_GEOMETRY", `${context}.${parentField} is unknown`);
    }
    encodedGeometryInteger(item.x, `${context}.x`);
    encodedGeometryInteger(item.y, `${context}.y`);
    encodedGeometryInteger(item.width, `${context}.width`);
    encodedGeometryInteger(item.height, `${context}.height`);
  };
  if (!Array.isArray(value.groups) || !Array.isArray(value.nodes) || !Array.isArray(value.ports) || !Array.isArray(value.edges)) {
    fail("E_OUTPUT_GEOMETRY", "geometry arrays are required");
  }
  const validateSortedUnique = (items, context) => {
    const ids = items.map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        fail("E_OUTPUT_GEOMETRY", `${context}[${index}] must be an object`);
      }
      return item.id;
    });
    if (ids.some((id) => typeof id !== "string") || new Set(ids).size !== ids.length) {
      fail("E_OUTPUT_GEOMETRY", `${context} IDs must be unique`);
    }
    const sorted = [...ids].sort(compareIds);
    if (canonical(ids) !== canonical(sorted)) fail("E_OUTPUT_GEOMETRY", `${context} must be sorted by ID`);
    return new Set(ids);
  };
  const actualGroupIds = validateSortedUnique(value.groups, "geometry.groups");
  const actualNodeIds = validateSortedUnique(value.nodes, "geometry.nodes");
  const actualPortIds = validateSortedUnique(value.ports, "geometry.ports");
  const actualEdgeIds = validateSortedUnique(value.edges, "geometry.edges");
  if (canonical([...actualGroupIds].sort()) !== canonical([...groupIds].sort()) || canonical([...actualNodeIds].sort()) !== canonical([...nodeIds].sort())) {
    fail("E_OUTPUT_GEOMETRY", "geometry omitted or changed semantic IDs");
  }
  for (const item of value.groups) {
    checkRect(item, GEOMETRY_GROUP_FIELDS, `geometry.groups.${item.id}`, "parent_id", groupIds);
    if (groupParents.get(item.id) !== item.parent_id) fail("E_OUTPUT_GEOMETRY", `geometry group ${item.id} changed parent_id`);
  }
  for (const item of value.nodes) {
    checkRect(item, GEOMETRY_NODE_FIELDS, `geometry.nodes.${item.id}`, "parent_id", groupIds);
    if (nodeParents.get(item.id) !== item.parent_id) fail("E_OUTPUT_GEOMETRY", `geometry node ${item.id} changed parent_id`);
  }
  for (const item of value.ports) {
    geometryObject(item, GEOMETRY_PORT_FIELDS, `geometry.ports.${item.id}`);
    if (typeof item.id !== "string" || !nodeIds.has(item.node_id)) fail("E_OUTPUT_GEOMETRY", "geometry port identity is invalid");
    encodedGeometryInteger(item.x, `geometry.ports.${item.id}.x`);
    encodedGeometryInteger(item.y, `geometry.ports.${item.id}.y`);
    encodedGeometryInteger(item.width, `geometry.ports.${item.id}.width`);
    encodedGeometryInteger(item.height, `geometry.ports.${item.id}.height`);
  }
  const expectedEdgeIds = input.edges.map((_, index) => `edge-${index}`).sort(compareIds);
  if (canonical([...actualEdgeIds].sort()) !== canonical(expectedEdgeIds)) fail("E_OUTPUT_GEOMETRY", "geometry omitted or changed semantic edges");
  for (const edge of value.edges) {
    geometryObject(edge, GEOMETRY_EDGE_FIELDS, `geometry.edges.${edge.id}`);
    if (!Array.isArray(edge.sections) || edge.sections.length === 0) fail("E_OUTPUT_GEOMETRY", `geometry edge ${edge.id} has no sections`);
    for (const section of edge.sections) {
      geometryObject(section, GEOMETRY_SECTION_FIELDS, `geometry.edges.${edge.id}.section`);
      geometryObject(section.start, GEOMETRY_POINT_FIELDS, "geometry edge start");
      geometryObject(section.end, GEOMETRY_POINT_FIELDS, "geometry edge end");
      encodedGeometryInteger(section.start.x, "geometry edge start.x");
      encodedGeometryInteger(section.start.y, "geometry edge start.y");
      encodedGeometryInteger(section.end.x, "geometry edge end.x");
      encodedGeometryInteger(section.end.y, "geometry edge end.y");
      if (!Array.isArray(section.bends)) fail("E_OUTPUT_GEOMETRY", "geometry edge bends must be an array");
      for (const bend of section.bends) {
        geometryObject(bend, GEOMETRY_POINT_FIELDS, "geometry edge bend");
        encodedGeometryInteger(bend.x, "geometry edge bend.x");
        encodedGeometryInteger(bend.y, "geometry edge bend.y");
      }
    }
  }
  return value;
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

async function canonicalPath(rawPath) {
  let absolute = resolve(rawPath);
  for (const alias of ["/tmp", "/var"]) {
    if (absolute !== alias && !absolute.startsWith(`${alias}${sep}`)) continue;
    try {
      const target = await realpath(alias);
      if (target !== alias) absolute = `${target}${absolute.slice(alias.length)}`;
    } catch {
      // Keep normal path errors at caller, where stable contract code is known.
    }
  }
  return absolute;
}

function directoryIdentity(entry) {
  return { dev: entry.dev, ino: entry.ino };
}

function sameDirectoryIdentity(left, right) {
  return left.dev === right.dev && left.ino === right.ino;
}

async function safeDirectory(rawPath, code, exitCode = 1) {
  const absolute = await canonicalPath(rawPath);
  let current = sep;
  let finalEntry;
  for (const part of absolute.split(sep).filter(Boolean)) {
    const next = join(current, part);
    let entry;
    try {
      entry = await lstat(next, { bigint: true });
    } catch {
      fail(code, `directory is unavailable: ${rawPath}`, exitCode);
    }
    if (entry.isSymbolicLink() || !entry.isDirectory()) {
      fail(code, `directory ancestry must not contain links: ${rawPath}`, exitCode);
    }
    current = next;
    finalEntry = entry;
  }
  if (!finalEntry) {
    try {
      finalEntry = await lstat(current, { bigint: true });
    } catch {
      fail(code, `directory is unavailable: ${rawPath}`, exitCode);
    }
  }
  try {
    const observed = await lstat(current, { bigint: true });
    if (
      observed.isSymbolicLink()
      || !observed.isDirectory()
      || !sameDirectoryIdentity(directoryIdentity(observed), directoryIdentity(finalEntry))
    ) {
      fail(code, `directory changed during validation: ${rawPath}`, exitCode);
    }
    return {
      path: current,
      realPath: await realpath(current),
      identity: directoryIdentity(observed),
    };
  } catch (error) {
    if (error instanceof AdapterError) throw error;
    fail(code, `directory is unavailable: ${rawPath}`, exitCode);
  }
}

async function assertDirectoryIdentity(snapshot, code = "E_OUTPUT_PATH", exitCode = 1) {
  const current = await safeDirectory(snapshot.path, code, exitCode);
  if (!sameDirectoryIdentity(current.identity, snapshot.identity)) {
    fail(code, "run directory was replaced during output", exitCode);
  }
  return current;
}

async function findDirectoryByIdentity(snapshot) {
  const parent = await safeDirectory(dirname(snapshot.path), "E_OUTPUT_PATH");
  for (const name of await readdir(parent.path)) {
    const candidate = join(parent.path, name);
    try {
      const entry = await lstat(candidate, { bigint: true });
      if (entry.isDirectory() && sameDirectoryIdentity(directoryIdentity(entry), snapshot.identity)) {
        return candidate;
      }
    } catch {
      // Candidate may disappear during a concurrent replacement.
    }
  }
  return null;
}

async function recoverDirectory(snapshot) {
  try {
    const current = await safeDirectory(snapshot.path, "E_OUTPUT_PATH");
    if (sameDirectoryIdentity(current.identity, snapshot.identity)) return current;
  } catch {
    // Original path may have been replaced or renamed.
  }
  const path = await findDirectoryByIdentity(snapshot);
  if (path === null) fail("E_OUTPUT_PATH", "original run directory is unavailable");
  const recovered = await safeDirectory(path, "E_OUTPUT_PATH");
  if (!sameDirectoryIdentity(recovered.identity, snapshot.identity)) {
    fail("E_OUTPUT_PATH", "original run directory identity changed");
  }
  return recovered;
}

async function cleanupTemporary(snapshot, name) {
  try {
    const recovered = await recoverDirectory(snapshot);
    await rm(join(recovered.path, name), { force: true });
    await syncDirectory(recovered);
  } catch {
    // Cleanup must never follow a replaced or linked output ancestry.
  }
}

async function validateDestination(parent, name, code, exitCode = 1) {
  let entry;
  try {
    entry = await lstat(join(parent, name), { bigint: true });
  } catch (error) {
    if (error?.code === "ENOENT") return;
    fail(code, `destination is unavailable: ${join(parent, name)}`, exitCode);
  }
  if (entry.isSymbolicLink() || !entry.isFile()) {
    fail(code, `destination must be absent or a regular file: ${join(parent, name)}`, exitCode);
  }
}

async function syncDirectory(snapshot) {
  await assertDirectoryIdentity(snapshot);
  let handle;
  try {
    handle = await open(snapshot.path, constants.O_RDONLY | (constants.O_DIRECTORY ?? 0) | (constants.O_NOFOLLOW ?? 0));
    await handle.sync();
  } finally {
    if (handle) await handle.close();
  }
  await assertDirectoryIdentity(snapshot);
}

async function validateOutputPaths(inputPath, outputPath, metadataPath, geometry = false) {
  const outputCode = geometry ? "E_OUTPUT_GEOMETRY" : "E_OUTPUT_PATH";
  const runCode = geometry ? "E_RUN_PATH" : "E_OUTPUT_PATH";
  const outputParent = await safeDirectory(dirname(outputPath), runCode, 2);
  const metadataParent = await safeDirectory(dirname(metadataPath), runCode, 2);
  const inputParent = await safeDirectory(dirname(inputPath), "E_INPUT_PATH", 2);
  if (
    outputParent.realPath !== metadataParent.realPath
    || outputParent.realPath !== inputParent.realPath
    || !sameDirectoryIdentity(outputParent.identity, metadataParent.identity)
    || !sameDirectoryIdentity(outputParent.identity, inputParent.identity)
  ) {
    fail(runCode, "input, output, and metadata must share one real run root", 2);
  }
  const outputName = basename(outputPath);
  const metadataName = basename(metadataPath);
  if (!outputName || !metadataName || outputName === metadataName) {
    fail(runCode, "output and metadata must be distinct files", 2);
  }
  await validateDestination(outputParent.realPath, outputName, outputCode, 2);
  await validateDestination(metadataParent.realPath, metadataName, outputCode, 2);
  return {
    inputPath: join(inputParent.realPath, basename(inputPath)),
    outputPath: join(outputParent.realPath, outputName),
    metadataPath: join(metadataParent.realPath, metadataName),
    runRoot: {
      path: outputParent.realPath,
      identity: outputParent.identity,
    },
  };
}

async function validateSingleOutput(path, runRoot = null, outputCode = "E_OUTPUT_PATH", runCode = outputCode) {
  const parent = await safeDirectory(dirname(path), runCode, 1);
  if (runRoot !== null) {
    await assertDirectoryIdentity(runRoot, runCode, 1);
    if (
      parent.realPath !== runRoot.path
      || !sameDirectoryIdentity(parent.identity, runRoot.identity)
    ) {
      fail(runCode, "destination escaped its real run root");
    }
  }
  const name = basename(path);
  await validateDestination(parent.realPath, name, outputCode, 1);
  return {
    path: join(parent.realPath, name),
    runRoot: runRoot ?? { path: parent.realPath, identity: parent.identity },
  };
}

function parseArguments(raw, worker = false) {
  const allowed = new Set(["--input", "--output", "--geometry", "--metadata", ...(worker ? ["--worker-output"] : [])]);
  const values = {};
  for (let index = 0; index < raw.length; index += 2) {
    const key = raw[index];
    const value = raw[index + 1];
    if (!allowed.has(key) || value === undefined || value.startsWith("--") || key in values) {
      fail("E_USAGE", "invalid ELK adapter arguments", 2);
    }
    values[key] = value;
  }
  for (const key of ["--input", "--metadata", ...(worker ? ["--worker-output"] : [])]) {
    if (!(key in values)) fail("E_USAGE", `${key} is required`, 2);
  }
  if (("--output" in values) === ("--geometry" in values)) {
    fail("E_USAGE", "exactly one of --output or --geometry is required", 2);
  }
  return values;
}

async function atomicWrite(path, raw, runRoot = null, previousRaw = undefined, outputCode = "E_OUTPUT_PATH", runCode = outputCode) {
  const destination = await validateSingleOutput(path, runRoot, outputCode, runCode);
  const parent = destination.runRoot;
  const name = basename(destination.path);
  const temporary = join(parent.path, `.${name}.tmp-${process.pid}-${Date.now()}`);
  let renamed = false;
  try {
    await assertDirectoryIdentity(parent);
    const handle = await open(temporary, "wx", 0o600);
    try {
      await handle.writeFile(raw);
      await handle.sync();
    } finally {
      await handle.close();
    }
    if (outputCode !== "E_OUTPUT_PATH") await validateDestination(parent.path, name, outputCode);
    await assertDirectoryIdentity(parent);
    await validateDestination(parent.path, name, "E_OUTPUT_PATH");
    await assertDirectoryIdentity(parent);
    await rename(temporary, destination.path);
    renamed = true;
    await assertDirectoryIdentity(parent);
    await syncDirectory(parent);
  } catch (error) {
    if (renamed && previousRaw !== undefined) {
      try {
        const recovered = await recoverDirectory(parent);
        const restorePath = join(recovered.path, name);
        if (previousRaw === null) {
          await rm(restorePath, { force: true });
          await syncDirectory(recovered);
        } else {
          await atomicWrite(restorePath, previousRaw, recovered, undefined, outputCode, runCode);
        }
      } catch {
        // Keep original error; pair-level rollback reports if restoration failed.
      }
    }
    throw error;
  } finally {
    await cleanupTemporary(parent, basename(temporary));
  }
}

async function readExisting(path, maximum, runRoot = null, outputCode = "E_OUTPUT_PATH", runCode = outputCode) {
  const destination = await validateSingleOutput(path, runRoot, outputCode, runCode);
  await assertDirectoryIdentity(destination.runRoot, runCode, 1);
  try {
    const previous = await readBounded(destination.path, maximum, "E_ATOMIC_WRITE");
    await assertDirectoryIdentity(destination.runRoot, runCode, 1);
    return previous;
  } catch (error) {
    if (error instanceof AdapterError && error.message.endsWith(" is unavailable")) return null;
    throw error;
  }
}

async function atomicWritePair(outputPath, outputRaw, metadataPath, metadataRaw, runRoot, options = {}) {
  const outputCode = options.outputCode ?? "E_OUTPUT_PATH";
  const runCode = options.runCode ?? outputCode;
  const metadataCode = options.metadataCode ?? outputCode;
  const metadataRunCode = options.metadataRunCode ?? runCode;
  await assertDirectoryIdentity(runRoot, runCode, 1);
  const [previousOutput, previousMetadata] = await Promise.all([
    readExisting(outputPath, options.outputMaximum ?? MAX_SVG_BYTES, runRoot, outputCode, runCode),
    readExisting(metadataPath, MAX_METADATA_BYTES, runRoot, metadataCode, metadataRunCode),
  ]);
  await assertDirectoryIdentity(runRoot, runCode, 1);
  let outputWritten = false;
  try {
    await atomicWrite(outputPath, outputRaw, runRoot, previousOutput, outputCode, runCode);
    outputWritten = true;
    await assertDirectoryIdentity(runRoot, runCode, 1);
    await atomicWrite(metadataPath, metadataRaw, runRoot, previousMetadata, metadataCode, metadataRunCode);
    await assertDirectoryIdentity(runRoot, runCode, 1);
  } catch (error) {
    if (outputWritten) {
      try {
        const recovered = await recoverDirectory(runRoot);
        const outputDestination = join(recovered.path, basename(outputPath));
        if (previousOutput === null) {
          await rm(outputDestination, { force: true });
          await syncDirectory(recovered);
        }
        else await atomicWrite(outputDestination, previousOutput, recovered, undefined, outputCode, runCode);
        if (previousMetadata !== null) {
          await atomicWrite(
            join(recovered.path, basename(metadataPath)),
            previousMetadata,
            recovered,
            undefined,
            metadataCode,
            metadataRunCode,
          );
        }
        await syncDirectory(recovered);
      } catch {
        fail("E_ATOMIC_ROLLBACK", "failed to restore last-known-good files");
      }
    }
    throw error;
  }
}

async function runWorker(args) {
  const inputPath = await canonicalPath(args["--input"]);
  const workerOutput = await canonicalPath(args["--worker-output"]);
  const outputDestination = await validateSingleOutput(workerOutput);
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
    if (args["--geometry"] !== undefined) {
      const rendererRaw = await readBounded(fileURLToPath(import.meta.url), MAX_ENGINE_BYTES, "E_ENGINE_IDENTITY");
      const geometry = renderGeometry(layout, input, rendererRaw);
      const rawGeometry = Buffer.from(`${canonical(geometry)}\n`, "utf8");
      validateGeometry(rawGeometry, input, geometry.engine.renderer_sha256);
      await atomicWrite(outputDestination.path, rawGeometry, outputDestination.runRoot);
    } else {
      const rawSvg = renderSvg(layout, input);
      validateSvg(rawSvg, input, true);
      await atomicWrite(outputDestination.path, rawSvg, outputDestination.runRoot);
    }
  } finally {
    await rm(vendorSnapshot, { recursive: true, force: true });
  }
}

async function runIsolatedWorker(args, outputPath) {
  const script = resolve(process.argv[1]);
  const work = await mkdtemp(join(tmpdir(), "readme-showcase-elk-"));
  const mode = args["--geometry"] !== undefined ? "--geometry" : "--output";
  const childArgs = [
    script,
    "--worker",
    "--input", args["--input"],
    mode, args[mode],
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
  const geometry = args["--geometry"] !== undefined;
  const artifactArgument = geometry ? args["--geometry"] : args["--output"];
  const [inputPath, outputPath, metadataPath] = await Promise.all([
    canonicalPath(args["--input"]),
    canonicalPath(artifactArgument),
    canonicalPath(args["--metadata"]),
  ]);
  if (new Set([inputPath, outputPath, metadataPath]).size !== 3) {
    fail("E_USAGE", "input, output, and metadata must be distinct files in one directory", 2);
  }
  const paths = await validateOutputPaths(inputPath, outputPath, metadataPath, geometry);
  const [{ raw: inputRaw, value: rawInput }] = await Promise.all([
    parseJson(paths.inputPath, MAX_INPUT_BYTES, "E_INPUT_SCHEMA"),
    verifyEngine(),
  ]);
  const input = validateEnvelope(rawInput);
  const work = await mkdtemp(join(tmpdir(), "readme-showcase-elk-controller-"));
  try {
    const snapshotInput = join(work, "input.json");
    await writeFile(snapshotInput, inputRaw, { flag: "wx", mode: 0o600 });
    const snapshotArgs = { ...args, "--input": snapshotInput };
    const firstPath = join(work, geometry ? "first.geometry.json" : "first.svg");
    const secondPath = join(work, geometry ? "second.geometry.json" : "second.svg");
    const first = await runIsolatedWorker(snapshotArgs, firstPath);
    if (first.code !== 0) throw workerFailure(first);
    const second = await runIsolatedWorker(snapshotArgs, secondPath);
    if (second.code !== 0) throw workerFailure(second);
    const [firstRaw, secondRaw] = await Promise.all([
      readBounded(firstPath, geometry ? MAX_GEOMETRY_BYTES : MAX_SVG_BYTES, geometry ? "E_OUTPUT_GEOMETRY" : "E_SVG_UNSAFE"),
      readBounded(secondPath, geometry ? MAX_GEOMETRY_BYTES : MAX_SVG_BYTES, geometry ? "E_OUTPUT_GEOMETRY" : "E_SVG_UNSAFE"),
    ]);
    const runHashes = [sha256(firstRaw), sha256(secondRaw)];
    if (!firstRaw.equals(secondRaw)) {
      fail("E_ENGINE_NONDETERMINISTIC", "ELK output differs across fresh runs", 1, "nondeterministic");
    }
    if (geometry) {
      const rendererRaw = await readBounded(fileURLToPath(import.meta.url), MAX_ENGINE_BYTES, "E_ENGINE_IDENTITY");
      validateGeometry(firstRaw, input, sha256(rendererRaw));
    } else {
      validateSvg(firstRaw, input, true);
    }
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
    await atomicWritePair(
      paths.outputPath,
      firstRaw,
      paths.metadataPath,
      metadataRaw,
      paths.runRoot,
      geometry
        ? {
          outputCode: "E_OUTPUT_GEOMETRY",
          metadataCode: "E_OUTPUT_GEOMETRY",
          runCode: "E_RUN_PATH",
          metadataRunCode: "E_RUN_PATH",
          outputMaximum: MAX_GEOMETRY_BYTES,
        }
        : undefined,
    );
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
