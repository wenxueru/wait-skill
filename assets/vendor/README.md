# Bundled dashboard libraries

The dashboard serves these pinned ES modules from the execution host.

| Module | Responsibility | Notices |
| --- | --- | --- |
| `dagre.mjs` | `@dagrejs/dagre` 3.1.3, including Graphlib, for graph layout | [MIT](dagre.LICENSE.txt) |
| `ui.mjs` | `d3-zoom` for graph gestures and `lit-html` for updating the task list and details | [Package licenses](ui.LICENSE.txt) |

From the repository root, use `npm ci` and `npm run build:dashboard` to rebuild `ui.mjs` and its license notices. Commit both outputs with any lockfile changes. Run `npm test` after updating either bundle.

For Dagre, take `package/dist/dagre.esm.js` and the notices from the pinned npm release. Remove the source-map footer and point the license header at the bundled notices.
