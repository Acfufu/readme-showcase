function escapeXml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export async function processSVG(input) {
  if (Object.keys(process.env).some((key) => /(TOKEN|SECRET|PASSWORD|CREDENTIAL|AWS_)/i.test(key))) {
    throw new Error("credential environment leaked");
  }
  if (
    input.style !== "compact"
    || input.aspectRatio !== "none"
    || input.exportFormat.join(",") !== "svg"
    || input.theme.fontFamily !== "Arial"
    || "customFontUrl" in input.theme
    || "customIcons" in input.theme
  ) {
    throw new Error("fixed presentation projection missing");
  }
  if (input.type === "c4") {
    if (input.elements.some((item) => !item.kind || "type" in item || "parentId" in item)) {
      throw new Error("invalid c4 projection");
    }
  } else if (input.edges.some((item) => !item.source || !item.target || "from" in item || "to" in item)) {
    throw new Error("invalid node-edge projection");
  }
  const items = input.type === "c4"
    ? [...input.elements, ...input.relationships.filter((item) => item.label)]
    : [...input.nodes, ...input.edges.filter((item) => item.label)];
  const labels = items.map((item) => item.label);
  const text = labels
    .map((label, index) => `<text x="20" y="${60 + index * 24}">${escapeXml(label)}</text>`)
    .join("");
  return {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="480" viewBox="0 0 1200 480" role="img"><title>${escapeXml(input.title)}</title>${text}</svg>`,
  };
}
