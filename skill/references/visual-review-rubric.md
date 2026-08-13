# Visual Review Rubric (frozen)

Reviewers score every criterion with a 0-10000 basis-point score AND must
cite the screenshot region (`x,y,width,height`) that supports the score.
Criteria are frozen; change requires a spec amendment.

Pixel-checked items (clipping, viewBox, readability, contrast) are NOT
rubric criteria: they are enforced by the screenshot gate itself. Rubric
criteria judge aesthetics only.

## Criteria

### composition_hierarchy
Does the visual show a clear display/section/supporting hierarchy? Is the
project name the dominant element, proof legible second, metadata smallest?
Evidence: cite the region containing each hierarchy level.

### slop_absence
Zero AI-slop signatures: centered template hero, three-equal feature rows,
generic gradient background, em-dashes, numbered eyebrows, decoration text
strips, fake-precise numbers, AI-sounding copy.
Evidence: cite any region containing a slop signature, or state "no slop
signatures found in any region" when clean.

### density_and_whitespace
Information density fits the 1200x360 canvas: breathing room, no text walls,
no sparse emptiness that reads as incomplete. Asymmetric imbalance and
abnormal whitespace are judged HERE as vision-LLM rubric criteria (they live
in the slop/density criteria), not as pixel checks.
Evidence: cite the densest region and the emptiest region.

### contrast_and_readability
Text readable on light AND dark GitHub themes; contrast >= 4.5:1 for body
text, >= 3:1 for large text. (The screenshot gate enforces the deterministic
floor; this criterion judges perceptual quality above the floor.)
Evidence: cite each text region evaluated.

### project_nativeness
The visual derives from repository evidence (identity, palette, proof) and
would not fit an unrelated project if the name disappeared.
Evidence: cite the identity/proof regions and state the provenance.
