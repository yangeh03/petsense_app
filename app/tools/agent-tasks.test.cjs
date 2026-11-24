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
  // 降级回信的标题与说明按「是否带核对结果」取文案；两种都不暗示状态正常或异常
  assert.match(result, /degradedLetterNotice\(hasEvidence\)/);
  assert.match(result, /degradedLetterLabel\(hasEvidence\)/);
  assert.match(read('services/agent-change.ts'), /不代表它的状态正常或异常/);
  assert.match(read('services/agent-change.ts'), /不代表它的整体状态正常或异常/);
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

test('sound event thresholds keep their exact count when the card is edited', () => {
  const { load } = require('./helpers/load-ts.cjs');
  const { thresholdDisplayValue, thresholdToValue, conditionSummary } = load(
    'components/agent/task-threshold.ts',
  );

  // 声音事件次数不得被当成秒再除以 60（3 次会变成 0）
  const sound = { metric: 'soundEventCount', op: 'at_least', value: 3, scope: 'daily' };
  assert.equal(thresholdDisplayValue(sound), '3');
  assert.equal(thresholdToValue(sound, '3'), 3);
  assert.equal(thresholdToValue(sound, '5'), 5);

  // 走动阈值用分钟显示且往返可逆
  const walk = { metric: 'walkSeconds', op: 'at_least', value: 7200, scope: 'window' };
  assert.equal(thresholdDisplayValue(walk), '120');
  assert.equal(thresholdToValue(walk, '120'), 7200);
  assert.equal(thresholdToValue(walk, '90'), 5400);
  assert.equal(thresholdToValue(walk, '1.5'), 90);

  // 非法输入（空、非数、负数、非整数次数）一律拒绝，不静默改成 0 或原值
  assert.equal(thresholdToValue(sound, ''), null);
  assert.equal(thresholdToValue(sound, '  '), null);
  assert.equal(thresholdToValue(sound, 'abc'), null);
  assert.equal(thresholdToValue(sound, '-1'), null);
  assert.equal(thresholdToValue(sound, '1.5'), null);
  assert.equal(thresholdToValue(walk, 'abc'), null);

  assert.equal(conditionSummary(sound), '单日 达到或超过 3 次');
  assert.equal(conditionSummary(walk), '累计 达到或超过 120 分钟');
});

test('the task card only sends a threshold the owner actually edited', () => {
  const card = read('components/agent/task-proposal-card.tsx');

  // 阈值来自统一换算模块，编辑标记决定是否覆盖数值
  assert.match(card, /thresholdDisplayValue/);
  assert.match(card, /thresholdToValue/);
  assert.match(card, /thresholdDirty/);
  assert.match(card, /setThresholdDirty\(true\)/);
  // 非法输入中止提交，不静默改值
  assert.match(card, /阈值需填非负数字/);
  assert.match(card, /if \(value === null\)/);
  // 不再有一刀切除以 60 的旧写法
  assert.doesNotMatch(card, /Math\.round\(plan\.condition\.value \/ 60\)/);
  assert.doesNotMatch(card, /\* unitSeconds/);
});
