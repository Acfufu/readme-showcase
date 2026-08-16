# Stage 5 · candidate

Stage role: author the candidate README files, `claim-map.json`, and
`asset-manifest.json` directly from target evidence.

Contract:

- Candidate assets bind to evidence, locale, exact bytes, and useful alt text.
- Every asset must declare either a supported `locale` or
  `language_neutral: true`, never both; locale metadata is never inferred from
  filenames, directories, or suffixes. Legacy visual markup such as
  `data-readme-language="neutral"` is an output annotation, not v2 manifest
  metadata.
- Keep v1 bilingual `README.md` / `README_zh.md` inputs readable and
  unchanged.

Next: [bundle-assemble.md](bundle-assemble.md).
