# Stage 4 · generation-request

Stage role: request candidate generation from the approved plan and scan
evidence. The orchestrator waits for an explicit plan and candidate instead of
inventing either.

The request consumes the plan written in [plan-import.md](plan-import.md) plus
`repository-evidence.json` from [scan.md](scan.md). For the opt-in compiled
route (`diagram_route: "compiled"`), the external candidate owns only the
locale README files, Claim Map v3, and Visual Spec v1 in
`stages/05-candidate/`; this is an opt-in compatibility path, not a new stage
or command, and it does not grant publication authority.

Next: [candidate.md](candidate.md).
