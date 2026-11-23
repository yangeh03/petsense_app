const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

const read = (file) => fs.readFileSync(path.join(__dirname, '../src', file), 'utf8');

test('delegation book is a registered route reachable from the companion header', () => {
  const layout = read('app/_layout.tsx');
  const companion = read('app/(tabs)/agent.tsx');
  const book = read('app/agent-tasks.tsx');

  assert.match(layout, /name="agent-tasks"[^]*title: '委托簿'/);
  assert.match(companion, /router\.push\('\/agent-tasks'\)/);
  assert.match(complement(companion), /查看委托簿/);
  // 委托簿操作直接生效（D3），状态与提示如实呈现
  assert.match(book, /agentTaskOp/);
  assert.match(book, /'pause' \| 'resume' \| 'cancel'/);
  assert.match(book, /暂停/);
  assert.match(book, /取消委托/);
  // 深链回原会话：设置伙伴页主题后返回
  assert.match(book, /setTopic\(task\.topicId/);
  assert.match(book, /router\.back\(\)/);
});

test('task proposal cards render plan text with baseline choice and editable resubmission', () => {
  const card = read('components/agent/task-proposal-card.tsx');
  const message = read('components/agent/agent-message.tsx');

  // 按 kind 分派：task_change 走委托卡，档案卡保持原路径
  assert.match(message, /TaskProposalCard/);
  assert.match(message, /block\.proposal\.kind === 'task_change'/);
  assert.match(card, /baselineStrategy|先按事实观察/);
  assert.match(card, /等基线就绪/);
  // 可编辑并重新提交归一化计划（spec §2）
  assert.match(card, /submitAgentProposalEdit/);
  assert.match(card, /按这版修改/);
  // 确认/拒绝共用决策端点；未确认不声称已开始
  assert.match(card, /decideAgentProposal/);
  assert.match(card, /确认后才开始观察/);
});

test('background task deliveries render as cards with old-client degradation', () => {
  const message = read('components/agent/agent-message.tsx');
  const result = read('components/agent/task-result-card.tsx');
  const services = read('services/agent.ts');

  // task_result 块进入 blocks 联合类型与渲染；未知块降级不计入已知块
  assert.match(services, /type: 'task_result'; task: AgentTaskResultBlock/);
  assert.match(message, /blocks\.filter\(\(block\) => block\.type === 'task_result'\)/);
  assert.match(message, /TaskResultCard/);
  assert.match(result, /委托提醒/);
  assert.match(result, /不代表它的状态正常或异常/);
  // 后台回合（无主人正文无图片）不渲染空气泡
  assert.match(message, /turn\.text \|\| images\.length \?/);
  // 委托簿服务接线
  assert.match(services, /fetchAgentTasks/);
  assert.match(services, /agentTaskOp/);
  assert.match(services, /submitAgentProposalEdit/);
});

function complement(source) {
  return source; // 别名保持断言可读
}
