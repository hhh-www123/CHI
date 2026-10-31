// The Python launcher uses the same path + NUL + bytes + NUL SHA256 format.
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

const entryFiles = ['index.html', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'tsconfig.json', 'vite.config.ts'];
const excluded = new Set(['__pycache__', '.vite-temp']);

export function frontendBuildInfo(root) {
  const files = entryFiles.filter(name => existsSync(resolve(root, name)) && statSync(resolve(root, name)).isFile());
  function collect(directory) {
    if (!existsSync(resolve(root, directory))) return;
    for (const entry of readdirSync(resolve(root, directory), { withFileTypes: true })) {
      if (excluded.has(entry.name)) continue;
      const name = `${directory}/${entry.name}`;
      if (entry.isDirectory()) collect(name);
      else if (entry.isFile()) files.push(name);
    }
  }
  for (const directory of ['src', 'public', 'scripts']) collect(directory);
  const hash = createHash('sha256');
  for (const name of files.sort()) {
    hash.update(name, 'utf8');
    hash.update('\0');
    hash.update(readFileSync(resolve(root, name)));
    hash.update('\0');
  }
  const { version } = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
  return { version, frontendRevision: hash.digest('hex') };
}

export function buildInfoPlugin(root) {
  let info;
  return {
    name: 'travel-build-info',
    apply: 'build',
    enforce: 'post',
    buildStart() { info = frontendBuildInfo(root); },
    generateBundle: {
      order: 'post',
      handler(_options, bundle) {
        // This asset is published with the successful build, never on its own.
        this.emitFile({
          type: 'asset', fileName: 'build-info.json',
          source: JSON.stringify({ ...info, builtAt: new Date().toISOString(), files: Object.keys(bundle).sort() }, null, 2),
        });
      },
    },
  };
}
