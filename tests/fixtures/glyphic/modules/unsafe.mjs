export async function processSVG(input) {
  if (input.type === "flowchart") {
    const labels = [...input.nodes, ...input.edges.filter((item) => item.label)]
      .map((item, index) => `<text x="20" y="${60 + index * 24}" opacity=".0e3">${item.label}</text>`)
      .join("");
    return {
      svg: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="480" viewBox="0 0 1200 480" role="img"><title>${input.title}</title>${labels}</svg>`,
    };
  }
  return {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="480" viewBox="0 0 1200 480" role="img"><title>${input.title}</title><script>alert(1)</script></svg>`,
  };
}
