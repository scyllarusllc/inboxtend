'use strict';
// Bilingual labels (English + 中文) for the single-task page. Every piece of interface text that has a Chinese
// translation here gets it shown right after the English; anything else (names, AI answers, email text) is untouched.
const ZH = {
  'All tasks': '全部任务', 'Loading task…': '正在加载任务…', 'Task not found': '找不到该任务', 'Could not load the task': '无法加载任务',
  'Back to all tasks': '返回全部任务',
  'Mark as done': '标记完成', 'Reopen task': '重新打开任务', 'Start working with AI': 'AI 开始处理', 'Show the AI plan': '查看 AI 方案',
  'Waiting': '等待中', 'Open the email': '打开邮件', 'Edit': '编辑', 'Edit task': '编辑任务', 'Save changes': '保存修改',
  'Images': '图片', 'Extracted text and AI explanation': '提取的文字与 AI 解读', 'History': '历史记录', 'From this email': '来自这封邮件', 'Read the full email': '阅读完整邮件',
  'How the AI would handle this': 'AI 会这样处理', 'Steps': '步骤', 'Confirm first': '先确认', 'Watch out for': '注意风险',
  'Reply draft (not sent)': '回复草稿（未发送）', 'Copy the draft': '复制草稿', 'Ask the AI again': '让 AI 重新生成',
  'Translate and explain all': '翻译并解释全部图片', 'Translate again (replaces the saved result)': '重新翻译（会替换已保存结果）',
  'Chinese translation and image interpretation': '中文翻译与图片解读', 'Use suggested task text': '采用建议的任务文字',
  'Rotate left': '向左旋转', 'Rotate right': '向右旋转', 'Download': '下载', 'Unnamed image': '未命名图片',
  'Loading history…': '正在加载历史…', 'No saved activity yet.': '还没有记录。', 'Task created': '任务已创建', 'AI plan saved': 'AI 方案已保存',
  'Images translated and explained': '图片已翻译并解释',
  'Title': '标题', 'Description': '描述', 'Owner': '负责人', 'Priority': '优先级', 'Due date (YYYY-MM-DD)': '截止日期（YYYY-MM-DD）',
  'Due time (HH:MM)': '截止时间（HH:MM）', 'Timezone (IANA)': '时区（IANA）', 'Project': '项目', 'Customer': '客户',
  'AI Agent (assignment only)': 'AI 代理（仅指派）', 'Status': '状态', 'No deadline': '没有截止日期',
  'AgentSteers mock handoff': 'AgentSteers 模拟交接',
  'Marked as done.': '已标记为完成。', 'Draft copied.': '草稿已复制。', 'Task changes saved locally.': '任务修改已保存。',
  'Image review saved with this task.': '图片分析已随任务保存。',
  'AI plan saved with this task. It is a guess: check it before acting.': 'AI 方案已随任务保存。这只是推测，行动前请核对。',
  'Thinking about how to handle this…': '正在思考如何处理…', 'Translating and explaining images…': '正在翻译并解释图片…',
  'Reading the images and translating the text…': '正在读取图片并翻译文字…', 'Still working, large images take longer…': '仍在处理，大图片需要更久…',
  'Loading task images…': '正在加载任务图片…',
  'Completed. Press to reopen': '已完成。点击重新打开',
};
const STATUS_ZH = {new: '新建', in_progress: '进行中', waiting: '等待中', done: '已完成'};
const PRIORITY_ZH = {low: '低', medium: '中', high: '高'};
const PATTERNS = [
  [/^Overdue by (\d+) days? · (.+)$/, (m) => '已逾期 ' + m[1] + ' 天 · ' + m[2]],
  [/^Due today · (.+)$/, (m) => '今天截止 · ' + m[1]],
  [/^Due tomorrow · (.+)$/, (m) => '明天截止 · ' + m[1]],
  [/^Due in (\d+) days · (.+)$/, (m) => m[1] + ' 天后截止 · ' + m[2]],
  [/^(low|medium|high) priority$/, (m) => PRIORITY_ZH[m[1]] + '优先级'],
  [/^High$/, () => '高优先级'],
  [/^Status saved: (.+)\.$/, (m) => '状态已保存：' + (STATUS_ZH[m[1].replaceAll(' ', '_')] || m[1]) + '。'],
  [/^Owner: (.+)$/, (m) => '负责人：' + m[1]],
  [/^Project: (.+)$/, (m) => '项目：' + m[1]],
  [/^Customer: (.+)$/, (m) => '客户：' + m[1]],
  [/^(low|medium|high) confidence$/, (m) => '置信度：' + PRIORITY_ZH[m[1]]],
  [/^Translate and explain all (\d+) images in Chinese$/, (m) => '翻译并解释全部 ' + m[1] + ' 张图片（中文）'],
  [/^Sending (\d+) images? to the AI… (\d+)s · estimate, about (\d+)s in total$/, (m) => '正在把 ' + m[1] + ' 张图片发送给 AI… 已用 ' + m[2] + ' 秒 · 预计约 ' + m[3] + ' 秒'],
  [/^(Reading the images and translating the text…|Still working, large images take longer…) (\d+)s · estimate, about (\d+)s in total$/,
    (m) => (ZH[m[1]] || '') + ' 已用 ' + m[2] + ' 秒 · 预计约 ' + m[3] + ' 秒'],
  [/^Done in (\d+)s\.$/, (m) => '已完成，用时 ' + m[1] + ' 秒。'],
  [/^Status of /, () => ''],
  [/^(Status → |Status → )(.+)$/, (m) => '状态 → ' + (STATUS_ZH[m[2].replaceAll(' ', '_')] || m[2])],
  [/^Status → (.+)$/, (m) => '状态 → ' + (STATUS_ZH[m[1].replaceAll(' ', '_')] || m[1])],
  [/^Owner → (.+)$/, (m) => '负责人 → ' + m[1]],
  [/^Priority → (.+)$/, (m) => '优先级 → ' + (PRIORITY_ZH[m[1]] || m[1])],
  [/^Created (.+) · Updated (.+)$/, (m) => '创建于 ' + m[1] + ' · 更新于 ' + m[2]],
  [/^Saved (.+?) · (.+)$/, (m) => '保存于 ' + m[1] + ' · AI 的猜测可能有误，请对照邮件核实。'],
];
function zhFor(text) {
  if (ZH[text]) return ZH[text];
  for (const [pattern, make] of PATTERNS) { const m = pattern.exec(text); if (m) return make(m); }
  return '';
}
const ZH_SKIP = /^(SCRIPT|STYLE|OPTION|SELECT|TEXTAREA|PRE|CODE|INPUT|CANVAS)$/;
function bilingual(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const found = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent || ZH_SKIP.test(parent.tagName) || parent.classList.contains('zh') || parent.closest('.message-body, .ai-plan, .ai-draft, .task-body, .email-excerpt, .image-review-item')) continue;
    const text = node.nodeValue.trim();
    if (!text) continue;
    const next = node.nextSibling;
    if (next && next.nodeType === 1 && next.classList.contains('zh')) continue;
    const zh = zhFor(text);
    if (zh) found.push([node, zh]);
  }
  for (const [node, zh] of found) {
    const span = document.createElement('span');
    span.className = 'zh';
    span.lang = 'zh';
    span.textContent = zh;
    node.after(span);
  }
}
// Keep newly drawn content bilingual too (a redraw, a lazily loaded panel, a toast).
(function watchBilingual() {
  const root = document.getElementById('main');
  if (!root) return;
  let queued = false;
  const run = () => { queued = false; observer.disconnect(); bilingual(root); observer.observe(root, {childList: true, subtree: true, characterData: true}); };
  const observer = new MutationObserver(() => { if (!queued) { queued = true; window.requestAnimationFrame(run); } });
  run();
})();
