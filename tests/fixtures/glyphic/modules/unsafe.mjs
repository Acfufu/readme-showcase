export async function processSVG(input) {
  return {
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="480" viewBox="0 0 1200 480" role="img"><title>${input.title}</title><script>alert(1)</script></svg>`,
  };
}
