const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.join(__dirname, '..');
const owner = process.env.GH_OWNER;
const repo = process.env.GH_REPO;
const token = process.env.GH_TOKEN;

if (!owner || !repo || !token) {
  console.error('发布需要设置 GH_TOKEN、GH_OWNER 和 GH_REPO。');
  process.exit(1);
}

const packageJson = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const releaseConfig = {
  ...packageJson.build,
  publish: [{ provider: 'github', owner, repo }],
};
const tempConfig = path.join(root, `.release-builder-${process.pid}.json`);
fs.writeFileSync(tempConfig, JSON.stringify(releaseConfig, null, 2));

const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const run = (args) => spawnSync(npm, args, { cwd: root, stdio: 'inherit', env: { ...process.env, GH_TOKEN: token } });

try {
  const backend = run(['run', 'build:backend']);
  if (backend.status !== 0) {
    process.exitCode = backend.status || 1;
  } else {
    const result = run(['exec', '--', 'electron-builder', '--config', tempConfig, '--publish', 'always']);
    process.exitCode = result.status ?? 1;
  }
} finally {
  fs.rmSync(tempConfig, { force: true });
}
