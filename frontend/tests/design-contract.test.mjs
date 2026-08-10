import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(frontendRoot, '..');

async function readOptional(relativePath) {
  try {
    return await readFile(path.join(repoRoot, relativePath), 'utf8');
  } catch (error) {
    if (error?.code === 'ENOENT') return '';
    throw error;
  }
}

test('root design contract defines stable layout rules for dynamic UI content', async () => {
  const design = await readOptional('DESIGN.md');

  assert.ok(design, 'DESIGN.md must exist at the repository root');
  assert.match(design, /状态可以改变内容[^\n]*不能无意改变工作区骨架/);
  assert.match(design, /min-width:\s*0/);
  assert.match(design, /minmax\(0,\s*1fr\)/);
  assert.match(design, /工具栏[\s\S]*筛选 N/);
  assert.match(design, /浮层[\s\S]*不参与主布局/);
  assert.match(design, /390[×x]844/);
  assert.match(design, /200%/);
  assert.match(design, /tokens\.css/);
  assert.match(design, /极端内容/);
  assert.match(design, /永久侧栏[^\n]*邮件导航/);
  assert.match(design, /(?:管理[^\n]*用户菜单|用户菜单[^\n]*管理)/);
});

test('agent instructions require reading DESIGN.md before UI work', async () => {
  const agents = await readOptional('AGENTS.md');

  assert.match(agents, /前端 UI、布局、组件或视觉修改前[^\n]*DESIGN\.md/);
  assert.match(agents, /DESIGN\.md[^\n]*事实来源/);
});
