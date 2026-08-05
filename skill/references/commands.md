# README Showcase Commands

Commands provide a small user vocabulary over the existing three modes. They
do not add pipeline stages, execution engines, or publication authority.

## Routing

1. Run an explicit command below when the request names it.
2. Treat a clearly implied command the same way: "review this README" routes to
   `audit`; "plan first" routes to `shape`.
3. If two commands materially fit, ask once before writing.
4. For a bare invocation, inspect the current README, dirty tree, and latest
   run. Recommend two or three concrete commands with targets and wait.
5. Natural-language requests remain valid. Commands are vocabulary, not a
   requirement that the user repeat their request.

Every route preserves unrelated changes. A local evaluation or preview never
authorizes commit, push, publication, or pull-request creation.

## `shape [target]`

Use when the user asks for a proposal, information architecture, visual
direction, or plan before implementation.

- Read repository evidence and the current README.
- Return audience, value, proof, first success, differentiator, limitations,
  proposed narrative, visual route, scope, and explicit anti-goals.
- Keep the proposal in chat. Do not create candidate files, edit the target,
  or start a generating pipeline stage.
- Stop for explicit approval or one correction round.

## `audit [target]`

Use for read-only diagnosis.

- Select audit-only mode.
- Check evidence coverage, first-screen clarity, real proof, quick-start
  observability, claims, commands, links, assets, locale parity, and visible
  limitations.
- Separate hard findings from advisory observations and cite reproducible
  evidence.
- Do not generate visuals, candidates, PR bundles, publish gates, or fixes.

## `redesign [target]`

Use for an approved whole README or substantial narrative rebuild.

- Select README mode and run the existing evidence-to-evaluation pipeline.
- Preserve product truth, supported behavior, repository constraints, and
  unrelated files while replacing weak narrative or visual composition.
- Require explicit visual-route decisions where the Skill already requires
  them. Motion and hybrid composition remain opt-in.
- Finish with verified local preview and diff. Stop before any Git or remote
  write.

## `polish [target]`

Use for a narrow refinement of an existing README section or asset integration.

- Select README mode but keep the named target as a hard scope boundary.
- Preserve existing identity, facts, behavior, section order, and surrounding
  content unless the user explicitly expands scope.
- Fix the shared cause when one issue affects localized variants or repeated
  markup, then audit every affected README.
- Finish with the smallest relevant validation and local diff.

If the concept or information architecture is the problem, report that and
recommend `redesign`; do not conceal a redesign inside polish.

## `visualize [target]`

Use for one README-only visual or a coordinated asset set.

- Select asset-only mode.
- Confirm asset type and locale coverage, then derive copy, palette, motif,
  composition, and proof from repository evidence.
- Prefer static SVG when it communicates the result. Use raster composition or
  motion only after explicit opt-in and retain the required editable sources
  and fallback.
- Validate wide and narrow GitHub presentation.
- Leave every README byte-for-byte unchanged until embedding is separately
  approved.

## Operational routes

- `status [target]` calls the existing pipeline `status` route.
- `resume [target]` calls the existing pipeline `resume` route.
- `preview [target]` calls the existing pipeline `preview` route.

These routes locate existing central run state. They never infer a new mode or
grant authority beyond the run's approved scope.
