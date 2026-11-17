import {readFile, writeFile} from "node:fs/promises";
import {build} from "esbuild";

const {metafile} = await build({
  stdin: {
    contents: [
      'export {select} from "d3-selection";',
      'export {zoom, zoomIdentity, zoomTransform} from "d3-zoom";',
      'export {html, nothing, render} from "lit-html";',
      'export {guard} from "lit-html/directives/guard.js";',
      'export {keyed} from "lit-html/directives/keyed.js";',
      'export {repeat} from "lit-html/directives/repeat.js";',
    ].join("\n"),
    resolveDir: process.cwd(),
  },
  bundle: true,
  format: "esm",
  minify: true,
  metafile: true,
  legalComments: "eof",
  outfile: "assets/vendor/ui.mjs",
});

const packages = new Set(Object.keys(metafile.inputs)
  .map(path => path.match(/(?:^|\/)node_modules\/((?:@[^/]+\/)?[^/]+)\//)?.[1])
  .filter(Boolean));
const notices = await Promise.all([...packages].sort().map(async name => {
  const root = `node_modules/${name}`;
  const {version} = JSON.parse(await readFile(`${root}/package.json`, "utf8"));
  return `${name} ${version}\n\n${(await readFile(`${root}/LICENSE`, "utf8")).trim()}`;
}));
await writeFile("assets/vendor/ui.LICENSE.txt", notices.join("\n\n---\n\n") + "\n");
