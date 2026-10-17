# Vendored mermaid.js — provenance

Source: `@mermaid-js/mermaid-cli@12.0.0` (the local install at `~/.nvm/versions/node/v24.21.0/lib/node_modules/@mermaid-js/mermaid-cli/`)
bundles `mermaid@12.1.0` (the only major version compatible with mmdc-v12).

Pinned URL of the upstream npm tarball:

    https://registry.npmjs.org/mermaid/-/mermaid-12.1.0.tgz

The vendored artifact is the **single-file IIFE bundle** `dist/mermaid.min.js`,
copied verbatim from:

    /home/nea/.nvm/versions/node/v24.21.0/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/mermaid/dist/mermaid.min.js

This is the package's esbuild `--format=iife` build. When loaded as a plain
`<script>` tag it sets `globalThis.mermaid = …` at the end of the bundle,
which is the only contract our wrapper uses. It does NOT require the ESM
chunks to be present alongside — the IIFE inlines the full runtime.

SHA-256 (verified post-copy): `6484afc32872a3aa16cac9a76ba1816a1ed4cc870a6593cc2e17757750f518b2`

Size: 5,493,176 bytes (~5.24 MiB)

Why this version: matches the major that the charter lane's `mmdc-v12` renders
with (per `agents/charter/skills-template/install-mermaid-cli.md`, the
charter-mmdc-toolchain config pins `mermaidCli: 12` and `mermaid-cli@12.0.0`
internally pins `mermaid@12.x`). Diagrams produced by charter's PNG-render
and the in-browser render from this bundle will agree on syntax + theming
defaults — parity with the reference PNGs the user expects to compare against.
