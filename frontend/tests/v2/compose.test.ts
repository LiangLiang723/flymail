import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import {
  AutosaveController,
  chooseInitialIdentity,
  createComposeModel,
  scheduleToEpochSeconds,
} from '../../src/features/compose/compose-state.ts';

function draft(version = 1) {
  return {
    id: 'd1', account_id: 'a1', identity_id: 'i1', thread_id: null, reply_to_message_id: null,
    subject: '', body_html: '', body_text: '', recipients: { to: [], cc: [], bcc: [] }, attachments: [],
    version, status: 'draft', send_state: 'idle', scheduled_at: null, send_message_id: '',
    created_at: 1, updated_at: 1, queued_at: null, sent_at: null,
  };
}

test('identity selection uses receiving account for reply and default for new mail', () => {
  const identities = [
    { id: 'i1', accountId: 'a1', isDefault: true },
    { id: 'i2', accountId: 'a2', isDefault: false },
  ];
  assert.equal(chooseInitialIdentity(identities, undefined), 'i1');
  assert.equal(chooseInitialIdentity(identities, 'a2'), 'i2');
});

test('autosave is versioned, single-flight and schedules next dirty version', async () => {
  const calls: Array<{ version: number; subject: string }> = [];
  let release: ((value: ReturnType<typeof draft>) => void) | undefined;
  const controller = new AutosaveController({
    initial: createComposeModel(draft()),
    save: async (model, expectedVersion) => {
      calls.push({ version: expectedVersion, subject: model.subject });
      return new Promise((resolve) => { release = resolve; });
    },
    debounceMs: 0,
  });
  controller.update({ subject: 'one' });
  const first = controller.flush();
  controller.update({ subject: 'two' });
  release?.({ ...draft(2), subject: 'one' });
  await first;
  assert.equal(calls.length, 2);
  release?.({ ...draft(3), subject: 'two' });
  await controller.waitForIdle();
  assert.deepEqual(calls, [{ version: 1, subject: 'one' }, { version: 2, subject: 'two' }]);
  assert.equal(controller.state, 'saved');
  assert.equal(controller.model.version, 3);
});

test('version conflict preserves local and remote drafts without overwrite', async () => {
  const controller = new AutosaveController({
    initial: createComposeModel(draft()),
    save: async () => {
      throw { status: 409, data: { error: { code: 'draft_version_conflict', details: { current: { ...draft(4), subject: 'remote' } } } } };
    },
    debounceMs: 0,
  });
  controller.update({ subject: 'local' });
  await assert.rejects(controller.flush());
  assert.equal(controller.state, 'conflict');
  assert.equal(controller.conflict?.local.subject, 'local');
  assert.equal(controller.conflict?.remote.subject, 'remote');
  assert.equal(controller.model.subject, 'local');
});

test('scheduled send converts datetime-local to absolute epoch seconds', () => {
  const epoch = scheduleToEpochSeconds('2026-08-02T15:30', 540);
  assert.equal(epoch, Date.parse('2026-08-02T06:30:00.000Z') / 1000);
});

test('compose modules keep editor lazy, uploads streaming, paths logical and mobile full page', async () => {
  const page = await readFile(new URL('../../src/features/compose/ComposePage.vue', import.meta.url), 'utf8');
  const editor = await readFile(new URL('../../src/features/compose/ComposeEditor.vue', import.meta.url), 'utf8');
  const attachments = await readFile(new URL('../../src/features/compose/DraftAttachments.vue', import.meta.url), 'utf8');
  const picker = await readFile(new URL('../../src/features/compose/ServerPathPicker.vue', import.meta.url), 'utf8');
  const schedule = await readFile(new URL('../../src/features/compose/ScheduleSendDialog.vue', import.meta.url), 'utf8');
  const conflict = await readFile(new URL('../../src/features/compose/DraftConflictDialog.vue', import.meta.url), 'utf8');
  assert.match(editor, /import\('@tiptap\/vue-3'\)/);
  assert.doesNotMatch(page, /@tiptap/);
  assert.match(attachments, /XMLHttpRequest/);
  assert.match(attachments, /upload\.onprogress/);
  assert.match(picker, /root_id|rootId/);
  assert.match(picker, /relative_path|relativePath/);
  assert.doesNotMatch(picker, /type="text"[^>]*absolute|host path/i);
  assert.match(schedule, /timezone/);
  assert.match(conflict, /local.*remote|remote.*local/s);
  assert.match(page, /cancel-send/);
  assert.match(page, /100dvh/);
});
