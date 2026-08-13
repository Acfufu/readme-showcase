#!/usr/bin/env node
// DOM-level text bounding boxes via resvg-js (usvg calculate_bbox, text
// converted to paths post-shaping). The viewport is the SVG's OWN
// width/height (parsed from width/height attributes, falling back to the
// viewBox aspect) scaled to the render width; bboxes are converted from SVG
// user units into the same render-pixel space so the Python side compares in
// ONE consistent space.
// Usage: node measure_bbox.mjs <svg> <width>

import { readFile } from "node:fs/promises";

function parseNumbers(raw) {
  // viewBox values may be space- OR comma-separated ("0 0 1200 360",
  // "0,0,1200,360"); both are legal per the SVG spec, so split on either.
  return (raw ?? "").trim().split(/[\s,]+/).filter(Boolean).map(Number);
}

async function main() {
  const [svgPath, widthArg] = process.argv.slice(2);
  if (!svgPath || !widthArg) {
    console.error("usage: node measure_bbox.mjs <svg> <width>");
    process.exit(2);
  }
  const width = Number.parseInt(widthArg, 10);
  let resvgModule;
  try {
    resvgModule = await import("@resvg/resvg-js");
  } catch {
    console.error("E_RESVG_JS_MISSING: @resvg/resvg-js not installed; see skill/vendor/resvg-js");
    process.exit(3);
  }
  const { Resvg } = resvgModule;
  const svg = await readFile(svgPath, "utf-8");
  const svgTag = svg.match(/<svg[^>]*>/i)?.[0] ?? "";
  const viewBox = parseNumbers(svgTag.match(/viewBox="([^"]*)"/i)?.[1]);
  const widthAttr = svgTag.match(/\swidth="([^"]*)"/i)?.[1];
  const heightAttr = svgTag.match(/\sheight="([^"]*)"/i)?.[1];
  // viewBox is authoritative; width/height attributes are fallbacks.
  const [vbX = 0, vbY = 0, vbW, vbH] = viewBox ?? [];
  const viewWidth = vbW ?? Number.parseFloat(widthAttr);
  const viewHeight = vbH ?? Number.parseFloat(heightAttr);
  if (!viewWidth || !viewHeight) {
    console.error("E_SVG_DIMS: svg must declare width/height or a viewBox");
    process.exit(4);
  }
  const scale = width / viewWidth;
  const resvg = new Resvg(svg, { fitTo: { mode: "width", value: width }, background: "white" });
  const viewport = { width, height: Math.round(viewHeight * scale) };
  const bbox = resvg.getBBox();
  if (!bbox) {
    console.log(JSON.stringify({ ok: true, viewport, text_bboxes: [] }));
    return;
  }
  // getBBox returns SVG user units; convert into render-pixel space.
  const text_bboxes = [{
    x: Math.round((bbox.x - vbX) * scale),
    y: Math.round((bbox.y - vbY) * scale),
    width: Math.round(bbox.width * scale),
    height: Math.round(bbox.height * scale),
  }];
  console.log(JSON.stringify({ ok: true, viewport, text_bboxes }));
}

main().catch((error) => {
  console.error(String(error));
  process.exit(1);
});
