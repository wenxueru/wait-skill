# Bundled graph layout

- Package: [`@dagrejs/dagre` 3.1.3](https://github.com/dagrejs/dagre), including Graphlib.
- Source: npm release `https://registry.npmjs.org/@dagrejs/dagre/-/dagre-3.1.3.tgz`, `package/dist/dagre.esm.js`.
- `dagre.mjs` retains the published code; only the license reference and source-map footer were replaced with the local license reference.
- License: [MIT notices](dagre.LICENSE.txt).

To update, replace the bundle and notices from a pinned release, then run `node --test tests/dashboard/*.test.mjs` from the repository root.
