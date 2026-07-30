#!/usr/bin/env node

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

const SOURCE_COMMIT = "ed79edb1624e2de78041611971a963efaea5e080";
const SOURCE_REPOSITORY = "https://github.com/MS-Teja/Glyphic";
const CORE_VERSION = "1.3.1";
const SCHEMA_VERSION = "1.1.1";
const LICENSE = "FSL-1.1-ALv2";
const TIMEOUT_MS = 30_000;
const MAX_INPUT_BYTES = 256 * 1024;
const MAX_LOCK_BYTES = 64 * 1024;
const MAX_SVG_BYTES = 2 * 1024 * 1024;
const MAX_TREE_BYTES = 256 * 1024 * 1024;
const MAX_TREE_FILES = 20_000;
const MAX_PROCESS_BYTES = 1024 * 1024;
const HELP = `Usage:
  node skill/scripts/render_glyphic.mjs \\
    --module-root /absolute/install/node_modules/@glyphicjs/core \\
    --engine-lock /absolute/glyphic-engine-lock.json \\
    --input RUN_DIR/diagram.glyphic.json \\
    --output RUN_DIR/diagram.svg \\
    --metadata RUN_DIR/diagram.engine.json

Exit codes:
  0  available and validated
  1  unavailable, invalid, timeout, or nondeterministic; existing output preserved
  2  usage, input, lock, identity, or runtime error
`;
const LOCK_FIELDS = new Set([
  "schema_version",
  "package_name",
  "package_version",
  "core_version",
  "schema_package_name",
  "schema_package_version",
  "npm_sri",
  "source_repository",
  "source_commit",
  "license_spdx",
  "license_file",
  "license_sha256",
  "package_json_sha256",
  "tree_sha256",
  "node_version",
]);
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
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

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

function exactObject(value, fields, context, code = "E_INPUT_SCHEMA") {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail(code, `${context} must be an object`, 2);
  }
  for (const field of fields) {
    if (!(field in value)) fail(code, `${context}.${field} is required`, 2);
  }
  for (const field of Object.keys(value)) {
    if (!fields.has(field)) fail(code, `${context}.${field} is forbidden`, 2);
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
  if (!ALLOWED_TYPES.has(input.diagram_type)) {
    fail("E_INPUT_SCHEMA", "input.diagram_type is not allowed", 2);
  }
  if (!ALLOWED_DIRECTIONS.has(input.direction)) {
    fail("E_INPUT_SCHEMA", "input.direction is not allowed", 2);
  }
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
  for (const group of groups) {
    if (group.parent_id !== null && !groupIds.has(group.parent_id)) {
      fail("E_INPUT_SCHEMA", `group ${group.id} references an unknown parent`, 2);
    }
    const seen = new Set([group.id]);
    let cursor = group.parent_id;
    while (cursor !== null) {
      if (seen.has(cursor)) fail("E_INPUT_SCHEMA", "group hierarchy contains a cycle", 2);
      seen.add(cursor);
      cursor = groups.find((item) => item.id === cursor)?.parent_id ?? null;
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

function projectInput(input) {
  const theme = {
    background: input.palette.background,
    nodeBackground: input.palette.node_background,
    nodeBorder: input.palette.node_border,
    nodeText: input.palette.node_text,
    edgeColor: input.palette.edge_color,
    edgeLabelColor: input.palette.edge_label_color,
  };
  if (input.diagram_type === "c4") {
    const c4Kind = {
      component: "container",
      service: "container",
      database: "database",
      person: "person",
      system: "system",
      external: "external",
      container: "container",
    };
    return {
      type: "c4",
      title: input.accessibility_title,
      direction: input.direction,
      theme,
      style: "compact",
      aspectRatio: "none",
      exportFormat: ["svg"],
      elements: [
        ...input.groups.map((item) => ({
          id: item.id,
          label: item.label,
          kind: "boundary",
          ...(item.parent_id === null ? {} : { parent: item.parent_id }),
        })),
        ...input.nodes.map((item) => ({
          id: item.id,
          label: item.label,
          kind: c4Kind[item.kind],
          ...(item.group_id === null ? {} : { parent: item.group_id }),
        })),
      ],
      relationships: input.edges.map((item) => ({
        from: item.source,
        to: item.target,
        ...(item.label === null ? {} : { label: item.label }),
      })),
    };
  }
  const shape = {
    component: "rounded",
    service: "service",
    database: "database",
    person: "person",
    system: "rectangle",
    external: "rectangle",
    container: "rectangle",
  };
  return {
    type: input.diagram_type,
    title: input.accessibility_title,
    direction: input.direction,
    routing: "orthogonal",
    theme,
    style: "compact",
    aspectRatio: "none",
    exportFormat: ["svg"],
    nodes: [
      ...input.groups.map((item) => ({
        id: item.id,
        label: item.label,
        shape: "rounded",
        ...(item.parent_id === null ? {} : { groupId: item.parent_id }),
      })),
      ...input.nodes.map((item) => ({
        id: item.id,
        label: item.label,
        shape: shape[item.kind],
        ...(item.group_id === null ? {} : { groupId: item.group_id }),
      })),
    ],
    edges: input.edges.map((item) => ({
      source: item.source,
      target: item.target,
      ...(item.label === null ? {} : { label: item.label }),
      style: "solid",
      arrow: "forward",
    })),
  };
}

async function readBounded(path, maximum, code) {
  let info;
  try {
    info = await stat(path);
  } catch {
    fail(code, `${basename(path)} is unavailable`, 2);
  }
  if (!info.isFile() || info.size > maximum) fail(code, `${basename(path)} exceeds contract`, 2);
  return readFile(path);
}

async function parseJson(path, maximum, code) {
  const raw = await readBounded(path, maximum, code);
  try {
    return { raw, value: JSON.parse(raw.toString("utf8")) };
  } catch {
    fail(code, `${basename(path)} is not valid UTF-8 JSON`, 2);
  }
}

async function treeSha256(root) {
  const files = [];
  let total = 0;
  async function walk(directory) {
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch {
      fail("E_ENGINE_TREE", "engine tree cannot be read", 2);
    }
    entries.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
    for (const entry of entries) {
      const path = join(directory, entry.name);
      if (entry.isSymbolicLink()) fail("E_ENGINE_TREE", "engine tree contains a symlink", 2);
      if (entry.isDirectory()) {
        await walk(path);
      } else if (entry.isFile()) {
        const info = await lstat(path);
        if (!info.isFile()) fail("E_ENGINE_TREE", "engine tree contains a special file", 2);
        total += info.size;
        if (files.length >= MAX_TREE_FILES || total > MAX_TREE_BYTES) {
          fail("E_ENGINE_TREE", "engine tree exceeds bounds", 2);
        }
        files.push(path);
      } else {
        fail("E_ENGINE_TREE", "engine tree contains a special file", 2);
      }
    }
  }
  await walk(root);
  files.sort((left, right) => {
    const leftName = relative(root, left).split(sep).join("/");
    const rightName = relative(root, right).split(sep).join("/");
    return leftName < rightName ? -1 : leftName > rightName ? 1 : 0;
  });
  const digest = createHash("sha256");
  for (const path of files) {
    const raw = await readFile(path);
    const name = relative(root, path).split(sep).join("/");
    digest.update(name, "utf8");
    digest.update("\0");
    digest.update(String(raw.length), "ascii");
    digest.update("\0");
    digest.update(raw);
    digest.update("\0");
  }
  return digest.digest("hex");
}

async function verifyEngine(moduleRoot, lockPath) {
  let rootInfo;
  try {
    rootInfo = await lstat(moduleRoot);
  } catch {
    fail("E_ENGINE_UNAVAILABLE", "Glyphic module root is unavailable", 1, "unavailable");
  }
  if (!resolve(moduleRoot).endsWith(`${sep}node_modules${sep}@glyphicjs${sep}core`)) {
    fail("E_ENGINE_IDENTITY", "module root must be isolated @glyphicjs/core", 2);
  }
  if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) {
    fail("E_ENGINE_IDENTITY", "module root must be a real directory", 2);
  }

  const { raw: lockRaw, value: lockValue } = await parseJson(
    lockPath,
    MAX_LOCK_BYTES,
    "E_ENGINE_LOCK",
  );
  const lock = exactObject(lockValue, LOCK_FIELDS, "engine_lock", "E_ENGINE_LOCK");
  const expected = {
    schema_version: 1,
    package_name: "@glyphicjs/core",
    package_version: CORE_VERSION,
    core_version: CORE_VERSION,
    schema_package_name: "@glyphicjs/schema",
    schema_package_version: SCHEMA_VERSION,
    source_repository: SOURCE_REPOSITORY,
    source_commit: SOURCE_COMMIT,
    license_spdx: LICENSE,
    license_file: "LICENSE",
    node_version: process.versions.node,
  };
  for (const [field, value] of Object.entries(expected)) {
    if (lock[field] !== value) fail("E_ENGINE_IDENTITY", `engine_lock.${field} mismatch`, 2);
  }
  for (const field of ["license_sha256", "package_json_sha256", "tree_sha256"]) {
    if (typeof lock[field] !== "string" || !SHA256_PATTERN.test(lock[field])) {
      fail("E_ENGINE_LOCK", `engine_lock.${field} must be SHA-256`, 2);
    }
  }
  if (typeof lock.npm_sri !== "string" || !/^sha512-[A-Za-z0-9+/]+={0,2}$/.test(lock.npm_sri)) {
    fail("E_ENGINE_LOCK", "engine_lock.npm_sri must be sha512 SRI", 2);
  }
  if (Number(process.versions.node.split(".")[0]) !== 22) {
    fail("E_ENGINE_RUNTIME", "Glyphic adapter requires Node 22", 2);
  }

  const nodeModules = dirname(dirname(moduleRoot));
  const schemaRoot = join(nodeModules, "@glyphicjs", "schema");
  const packagePath = join(moduleRoot, "package.json");
  const schemaPackagePath = join(schemaRoot, "package.json");
  const licensePath = join(moduleRoot, "LICENSE");
  const [{ raw: packageRaw, value: packageValue }, { value: schemaValue }, licenseRaw, treeDigest] =
    await Promise.all([
      parseJson(packagePath, MAX_LOCK_BYTES, "E_ENGINE_IDENTITY"),
      parseJson(schemaPackagePath, MAX_LOCK_BYTES, "E_ENGINE_IDENTITY"),
      readBounded(licensePath, MAX_LOCK_BYTES, "E_ENGINE_LICENSE"),
      treeSha256(nodeModules),
    ]);
  if (
    packageValue.name !== "@glyphicjs/core"
    || packageValue.version !== CORE_VERSION
    || packageValue.license !== LICENSE
    || packageValue.type !== "module"
    || packageValue.exports?.["."]?.import !== "./dist/index.js"
    || schemaValue.name !== "@glyphicjs/schema"
    || schemaValue.version !== SCHEMA_VERSION
  ) {
    fail("E_ENGINE_IDENTITY", "installed Glyphic package identity mismatch", 2);
  }
  if (sha256(packageRaw) !== lock.package_json_sha256) {
    fail("E_ENGINE_IDENTITY", "Glyphic package.json digest mismatch", 2);
  }
  if (sha256(licenseRaw) !== lock.license_sha256) {
    fail("E_ENGINE_LICENSE", "Glyphic license digest mismatch", 2);
  }
  if (treeDigest !== lock.tree_sha256) {
    fail("E_ENGINE_TREE", "Glyphic install tree digest mismatch", 2);
  }
  return {
    lock,
    lockRaw,
    moduleEntry: join(moduleRoot, "dist", "index.js"),
  };
}

function decodeXml(value) {
  return value
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

function validateSvg(raw, input, semantic = false) {
  if (!Buffer.isBuffer(raw) || raw.length < 1 || raw.length > MAX_SVG_BYTES) {
    fail("E_SVG_UNSAFE", "SVG exceeds byte contract");
  }
  const svg = raw.toString("utf8");
  if (Buffer.byteLength(svg, "utf8") !== raw.length || svg.includes("\uFFFD")) {
    fail("E_SVG_UNSAFE", "SVG must be valid UTF-8");
  }
  const lower = svg.toLowerCase();
  for (const marker of [
    "<!doctype",
    "<!entity",
    "<script",
    "<style",
    "<foreignobject",
    "<image",
    "<animate",
    "<set",
    "<mpath",
  ]) {
    if (lower.includes(marker)) fail("E_SVG_UNSAFE", `SVG contains forbidden ${marker}`);
  }
  if (/\son[a-z]+\s*=/i.test(svg) || /@import/i.test(svg)) {
    fail("E_SVG_UNSAFE", "SVG contains active content");
  }
  if (/\b(?:href|xlink:href)\s*=\s*["'](?!#)[^"']*["']/i.test(svg)) {
    fail("E_SVG_UNSAFE", "SVG contains external reference");
  }
  const ids = [...svg.matchAll(/\bid\s*=\s*["']([^"']+)["']/gi)]
    .map((match) => match[1]);
  if (
    ids.some((id) => !ID_PATTERN.test(id))
    || new Set(ids).size !== ids.length
  ) {
    fail("E_SVG_UNSAFE", "SVG ids must be unique bounded identifiers");
  }
  const idSet = new Set(ids);
  for (const match of svg.matchAll(/url\s*\(\s*([^)]*?)\s*\)/gi)) {
    const reference = match[1].match(/^["']?#([A-Za-z0-9][A-Za-z0-9_-]{0,63})["']?$/);
    if (reference === null || !idSet.has(reference[1])) {
      fail("E_SVG_UNSAFE", "SVG URL must reference a defined local id");
    }
  }
  for (const match of svg.matchAll(/\b(?:href|xlink:href)\s*=\s*["']#([^"']+)["']/gi)) {
    if (!ID_PATTERN.test(match[1]) || !idSet.has(match[1])) {
      fail("E_SVG_UNSAFE", "SVG href must reference a defined local id");
    }
  }
  const openTag = svg.match(/<svg\b([^>]*)>/i);
  if (!openTag) fail("E_SVG_UNSAFE", "SVG root is missing");
  if (!/\bwidth\s*=\s*["'](?:[1-9]\d*)(?:\.\d+)?["']/i.test(openTag[1])) {
    fail("E_SVG_UNSAFE", "SVG width must be positive and unitless");
  }
  if (!/\bheight\s*=\s*["'](?:[1-9]\d*)(?:\.\d+)?["']/i.test(openTag[1])) {
    fail("E_SVG_UNSAFE", "SVG height must be positive and unitless");
  }
  if (!/\bviewBox\s*=\s*["']\s*-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+[1-9]\d*(?:\.\d+)?\s+[1-9]\d*(?:\.\d+)?\s*["']/i.test(openTag[1])) {
    fail("E_SVG_UNSAFE", "SVG viewBox must have positive dimensions");
  }
  if (!/\brole\s*=\s*["']img["']/i.test(openTag[1])) {
    fail("E_SVG_UNSAFE", "SVG role must be img");
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
  const allowed = new Set([
    "--module-root",
    "--engine-lock",
    "--input",
    "--output",
    "--metadata",
    ...(worker ? ["--worker-output"] : []),
  ]);
  const values = {};
  for (let index = 0; index < raw.length; index += 2) {
    const key = raw[index];
    const value = raw[index + 1];
    if (!allowed.has(key) || value === undefined || value.startsWith("--") || key in values) {
      fail("E_USAGE", "invalid Glyphic adapter arguments", 2);
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
    readExisting(metadataPath, MAX_LOCK_BYTES),
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
  const moduleRoot = resolve(args["--module-root"]);
  const lockPath = resolve(args["--engine-lock"]);
  const inputPath = resolve(args["--input"]);
  const workerOutput = resolve(args["--worker-output"]);
  const [{ value: rawInput }, engine] = await Promise.all([
    parseJson(inputPath, MAX_INPUT_BYTES, "E_INPUT_SCHEMA"),
    verifyEngine(moduleRoot, lockPath),
  ]);
  const input = validateEnvelope(rawInput);
  const projected = projectInput(input);
  const moduleUrl = pathToFileURL(engine.moduleEntry).href;
  let implementation;
  try {
    implementation = await import(moduleUrl);
  } catch {
    fail("E_ENGINE_IMPORT", "Glyphic processSVG import failed");
  }
  if (typeof implementation.processSVG !== "function") {
    fail("E_ENGINE_IMPORT", "Glyphic processSVG export is unavailable");
  }
  let result;
  try {
    result = await implementation.processSVG(projected);
  } catch (error) {
    fail("E_ENGINE_RENDER", `Glyphic processSVG failed: ${error instanceof Error ? error.message : "unknown error"}`);
  }
  if (!result || typeof result.svg !== "string") {
    fail("E_ENGINE_RENDER", "Glyphic processSVG returned no SVG");
  }
  const rawSvg = Buffer.from(result.svg, "utf8");
  validateSvg(rawSvg, input, false);
  await atomicWrite(workerOutput, rawSvg);
}

async function runIsolatedWorker(baseArgs, outputPath) {
  const script = resolve(process.argv[1]);
  const work = await mkdtemp(join(tmpdir(), "readme-showcase-glyphic-"));
  const childArgs = [
    script,
    "--worker",
    "--module-root",
    baseArgs["--module-root"],
    "--engine-lock",
    baseArgs["--engine-lock"],
    "--input",
    baseArgs["--input"],
    "--output",
    baseArgs["--output"],
    "--metadata",
    baseArgs["--metadata"],
    "--worker-output",
    outputPath,
  ];
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(process.execPath, childArgs, {
      cwd: work,
      detached: process.platform !== "win32",
      env: {
        PATH: process.env.PATH ?? "",
        LC_ALL: "C",
        TZ: "UTC",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    const finish = async (error, code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      await rm(work, { recursive: true, force: true });
      if (error) rejectPromise(error);
      else resolvePromise({ code, stderr: Buffer.concat(stderr).toString("utf8") });
    };
    const overflow = () => {
      if (process.platform !== "win32") {
        try { process.kill(-child.pid, "SIGKILL"); } catch {}
      } else {
        child.kill("SIGKILL");
      }
      finish(new AdapterError("E_ENGINE_OUTPUT_LIMIT", "Glyphic worker output exceeded limit"));
    };
    child.stdout.on("data", (chunk) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > MAX_PROCESS_BYTES) overflow();
      else stdout.push(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderrBytes += chunk.length;
      if (stderrBytes > MAX_PROCESS_BYTES) overflow();
      else stderr.push(chunk);
    });
    child.on("error", (error) => finish(
      new AdapterError("E_ENGINE_PROCESS", `Glyphic worker failed: ${error.message}`),
    ));
    child.on("close", (code) => finish(null, code ?? 1));
    const timer = setTimeout(() => {
      if (process.platform !== "win32") {
        try { process.kill(-child.pid, "SIGKILL"); } catch {}
      } else {
        child.kill("SIGKILL");
      }
      finish(new AdapterError("E_ENGINE_TIMEOUT", "Glyphic worker exceeded 30 seconds", 1, "timeout"));
    }, TIMEOUT_MS);
  });
}

function parseWorkerError(stderr) {
  const match = stderr.match(/^([A-Z][A-Z0-9_]+): ([^\r\n]*)/m);
  return match ? new AdapterError(match[1], match[2]) : new AdapterError(
    "E_ENGINE_RENDER",
    "Glyphic worker failed",
  );
}

function workerFailure(result) {
  if (result.code === 13) {
    return new AdapterError(
      "E_ENGINE_TIMEOUT",
      "Glyphic worker left render promise unresolved",
      1,
      "timeout",
    );
  }
  return parseWorkerError(result.stderr);
}

async function runController(args) {
  for (const key of ["--module-root", "--engine-lock"]) {
    if (!args[key].startsWith(sep)) fail("E_USAGE", `${key} must be absolute`, 2);
  }
  const inputPath = resolve(args["--input"]);
  const outputPath = resolve(args["--output"]);
  const metadataPath = resolve(args["--metadata"]);
  if (dirname(inputPath) !== dirname(outputPath) || dirname(outputPath) !== dirname(metadataPath)) {
    fail("E_USAGE", "input, output, and metadata must share a directory", 2);
  }
  const [{ raw: inputRaw, value: rawInput }, engine] = await Promise.all([
    parseJson(inputPath, MAX_INPUT_BYTES, "E_INPUT_SCHEMA"),
    verifyEngine(resolve(args["--module-root"]), resolve(args["--engine-lock"])),
  ]);
  const input = validateEnvelope(rawInput);
  const work = await mkdtemp(join(tmpdir(), "readme-showcase-glyphic-controller-"));
  try {
    const firstPath = join(work, "first.svg");
    const secondPath = join(work, "second.svg");
    const first = await runIsolatedWorker(args, firstPath);
    if (first.code !== 0) throw workerFailure(first);
    const second = await runIsolatedWorker(args, secondPath);
    if (second.code !== 0) throw workerFailure(second);
    const [firstRaw, secondRaw] = await Promise.all([
      readBounded(firstPath, MAX_SVG_BYTES, "E_SVG_UNSAFE"),
      readBounded(secondPath, MAX_SVG_BYTES, "E_SVG_UNSAFE"),
    ]);
    const runHashes = [sha256(firstRaw), sha256(secondRaw)];
    if (!firstRaw.equals(secondRaw)) {
      fail("E_ENGINE_NONDETERMINISTIC", "Glyphic output differs across fresh runs", 1, "nondeterministic");
    }
    validateSvg(firstRaw, input, true);
    const outputDigest = runHashes[0];
    const metadata = {
      schema_version: 1,
      engine_kind: "glyphic",
      source_commit: SOURCE_COMMIT,
      package_version: CORE_VERSION,
      core_version: CORE_VERSION,
      engine_schema_version: SCHEMA_VERSION,
      package_sha256: engine.lock.package_json_sha256,
      tree_sha256: engine.lock.tree_sha256,
      sri: engine.lock.npm_sri,
      license_spdx: LICENSE,
      license_sha256: engine.lock.license_sha256,
      lock_sha256: sha256(engine.lockRaw),
      node_version: process.versions.node,
      platform: process.platform,
      architecture: process.arch,
      input_sha256: sha256(inputRaw),
      theme_sha256: sha256(Buffer.from(canonical(input.palette))),
      output_sha256: outputDigest,
      run_hashes: runHashes,
      validation: "pass",
      fallback_state: "preserved",
    };
    const metadataRaw = Buffer.from(`${canonical(metadata)}\n`, "utf8");
    await atomicWritePair(outputPath, firstRaw, metadataPath, metadataRaw);
    process.stdout.write(`${JSON.stringify({
      schema_version: 1,
      status: "available",
      output_sha256: outputDigest,
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
      const args = parseArguments(process.argv.slice(3), true);
      await runWorker(args);
      return;
    }
    const args = parseArguments(process.argv.slice(2), false);
    await runController(args);
  } catch (error) {
    const known = error instanceof AdapterError
      ? error
      : new AdapterError("E_INTERNAL", "unexpected adapter failure", 1);
    process.stdout.write(`${JSON.stringify({ schema_version: 1, status: known.status })}\n`);
    process.stderr.write(`${known.code}: ${known.message}\n`);
    process.exitCode = known.exitCode;
  }
}

await main();
