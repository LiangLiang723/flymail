/**
 * FlyMail Docker 版本同步脚本。
 *
 * VERSION 是唯一事实来源，同步到：
 * - package.json
 * - frontend/package.json
 * - docker-compose.yml 镜像标签
 * - README.md 镜像标签和可选 version badge
 */
const fs = require('fs');
const path = require('path');

const rootDir = path.join(__dirname, '..');
const versionFile = path.join(rootDir, 'VERSION');
const version = fs.readFileSync(versionFile, 'utf-8').trim();

if (!/^\d+\.\d+\.\d+$/.test(version)) {
  console.error(`VERSION 文件内容非法: "${version}"`);
  process.exit(1);
}

function updatePackageJson(filePath) {
  const value = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  value.version = version;
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, 'utf-8');
}

function replaceRequired(filePath, pattern, replacement, label) {
  const current = fs.readFileSync(filePath, 'utf-8');
  const next = current.replace(pattern, replacement);
  if (next === current && !current.includes(replacement)) {
    console.error(`未能同步 ${label}`);
    process.exit(1);
  }
  fs.writeFileSync(filePath, next, 'utf-8');
}

console.log(`同步版本号: ${version}`);

updatePackageJson(path.join(rootDir, 'package.json'));
console.log('  ✓ package.json');

updatePackageJson(path.join(rootDir, 'frontend', 'package.json'));
console.log('  ✓ frontend/package.json');

replaceRequired(
  path.join(rootDir, 'docker-compose.yml'),
  /image:\s*benxianyu\/flymail:\d+\.\d+\.\d+/g,
  `image: benxianyu/flymail:${version}`,
  'docker-compose.yml 镜像标签',
);
console.log('  ✓ docker-compose.yml');

const readmePath = path.join(rootDir, 'README.md');
let readme = fs.readFileSync(readmePath, 'utf-8');
readme = readme
  .replace(/benxianyu\/flymail:\d+\.\d+\.\d+/g, `benxianyu/flymail:${version}`)
  .replace(/badge\/version-\d+\.\d+\.\d+/g, `badge/version-${version}`);
fs.writeFileSync(readmePath, readme, 'utf-8');
console.log('  ✓ README.md');

console.log('版本号同步完成');
