import assert from 'node:assert/strict';
import test from 'node:test';

async function loadConversationBodyModule() {
  return import('../src/utils/conversation-body.ts').catch(() => null);
}

test('conversation plain text keeps only the current reply', async () => {
  const module = await loadConversationBodyModule();
  assert.ok(module, 'conversation body quote trimming utility must exist');
  const stripQuotedText = module?.stripConversationQuotedText;
  assert.equal(typeof stripQuotedText, 'function');
  if (typeof stripQuotedText !== 'function') return;

  const body = [
    '这周改动已经完成。',
    '',
    'From: Neal Chen <neal@example.com>',
    'Sent: Thursday, August 20, 2026 1:51 PM',
    'To: Jimmy Lu <jimmy@example.com>',
    'Cc: Team <team@example.com>',
    '',
    '这是上一封邮件的正文。',
  ].join('\n');

  assert.equal(stripQuotedText(body), '这周改动已经完成。');
  assert.equal(
    stripQuotedText('最新回复\n\nOn Thu, Aug 20, 2026 at 1:51 PM Neal Chen wrote:\n旧邮件'),
    '最新回复',
  );
  assert.equal(
    stripQuotedText('From: 这是正文里讨论的字段\n但后面没有 Sent/To 邮件头'),
    'From: 这是正文里讨论的字段\n但后面没有 Sent/To 邮件头',
  );
});

test('conversation html removes common trailing reply history blocks', async () => {
  const module = await loadConversationBodyModule();
  assert.ok(module, 'conversation body quote trimming utility must exist');
  const stripQuotedHtml = module?.stripConversationQuotedHtml;
  assert.equal(typeof stripQuotedHtml, 'function');
  if (typeof stripQuotedHtml !== 'function') return;

  assert.equal(
    stripQuotedHtml('<p>最新回复</p><blockquote data-flymail-quote="reply"><p>旧邮件</p></blockquote>'),
    '<p>最新回复</p>',
  );
  assert.equal(
    stripQuotedHtml('<p>最新回复</p><div class="gmail_quote">On ... wrote:<div>旧邮件</div></div>'),
    '<p>最新回复</p>',
  );
  assert.equal(
    stripQuotedHtml('<p>最新回复</p><hr><div><b>From:</b> Neal</div><div><b>Sent:</b> Thu</div><div><b>To:</b> Jimmy</div><div><b>Cc:</b> Team</div><p>旧邮件</p>'),
    '<p>最新回复</p>',
  );
  assert.equal(
    stripQuotedHtml('<p>最新回复里主动引用一句：</p><blockquote>这是用户自己引用的内容</blockquote><p>继续正文</p>'),
    '<p>最新回复里主动引用一句：</p><blockquote>这是用户自己引用的内容</blockquote><p>继续正文</p>',
  );
});
