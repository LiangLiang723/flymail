import { normalizeApiError } from '../../shared/api/errors.ts';

export interface ComposeRecipient {
  address: string;
  display_name?: string;
}

export interface ComposeRecipients {
  to: ComposeRecipient[];
  cc: ComposeRecipient[];
  bcc: ComposeRecipient[];
}

export interface DraftAttachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  position_index: number;
  created_at: number;
}

export interface DraftRecord {
  id: string;
  account_id: string;
  identity_id: string;
  thread_id: string | null;
  reply_to_message_id: string | null;
  subject: string;
  body_html: string;
  body_text: string;
  recipients: ComposeRecipients;
  attachments: DraftAttachment[];
  version: number;
  status: string;
  send_state: string;
  scheduled_at: number | null;
  send_message_id: string;
  created_at: number;
  updated_at: number;
  queued_at: number | null;
  sent_at: number | null;
}

export interface ComposeModel extends DraftRecord {}
export type AutosaveState = 'clean' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'failed';

export interface IdentityChoice {
  id: string;
  accountId: string;
  isDefault?: boolean;
  signatureHtml?: string;
  replyTo?: string;
}

export function chooseInitialIdentity(identities: IdentityChoice[], receivingAccountId?: string): string {
  if (receivingAccountId) {
    const receiving = identities.find((identity) => identity.accountId === receivingAccountId);
    if (receiving) return receiving.id;
  }
  return identities.find((identity) => identity.isDefault)?.id || identities[0]?.id || '';
}

export function createComposeModel(draft: DraftRecord): ComposeModel {
  return {
    ...draft,
    recipients: {
      to: [...(draft.recipients?.to || [])],
      cc: [...(draft.recipients?.cc || [])],
      bcc: [...(draft.recipients?.bcc || [])],
    },
    attachments: [...(draft.attachments || [])],
  };
}

export function scheduleToEpochSeconds(datetimeLocal: string, timezoneOffsetMinutes = -new Date().getTimezoneOffset()): number {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(datetimeLocal.trim());
  if (!match) throw new Error('invalid schedule datetime');
  const [, year, month, day, hour, minute, second = '0'] = match;
  const utcMillis = Date.UTC(
    Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second),
  ) - timezoneOffsetMinutes * 60_000;
  return Math.floor(utcMillis / 1000);
}

interface AutosaveOptions {
  initial: ComposeModel;
  save: (model: ComposeModel, expectedVersion: number) => Promise<DraftRecord>;
  debounceMs?: number;
  setTimeoutFn?: typeof setTimeout;
  clearTimeoutFn?: typeof clearTimeout;
}

export class AutosaveController {
  private readonly saveAction: AutosaveOptions['save'];
  private readonly debounceMs: number;
  private readonly setTimeoutFn: typeof setTimeout;
  private readonly clearTimeoutFn: typeof clearTimeout;
  private timer?: ReturnType<typeof setTimeout>;
  private inFlight?: Promise<void>;
  private dirtyGeneration = 0;
  private savedGeneration = 0;
  model: ComposeModel;
  state: AutosaveState = 'clean';
  error = '';
  conflict?: { local: ComposeModel; remote: ComposeModel };

  constructor(options: AutosaveOptions) {
    this.model = createComposeModel(options.initial);
    this.saveAction = options.save;
    this.debounceMs = Math.max(0, options.debounceMs ?? 700);
    this.setTimeoutFn = options.setTimeoutFn || setTimeout;
    this.clearTimeoutFn = options.clearTimeoutFn || clearTimeout;
  }

  update(patch: Partial<ComposeModel>): void {
    this.model = createComposeModel({ ...this.model, ...patch });
    this.dirtyGeneration += 1;
    this.state = 'dirty';
    this.error = '';
    this.schedule();
  }

  flush(): Promise<void> {
    if (this.timer) {
      this.clearTimeoutFn(this.timer);
      this.timer = undefined;
    }
    if (this.inFlight) return this.inFlight;
    if (this.dirtyGeneration === this.savedGeneration) {
      if (this.state !== 'conflict') this.state = 'saved';
      return Promise.resolve();
    }
    const generation = this.dirtyGeneration;
    const snapshot = createComposeModel(this.model);
    const expectedVersion = this.model.version;
    this.state = 'saving';
    const request = this.saveAction(snapshot, expectedVersion)
      .then((saved) => {
        const newerChangesExist = this.dirtyGeneration > generation;
        this.savedGeneration = generation;
        this.model = newerChangesExist
          ? { ...this.model, version: saved.version, attachments: [...saved.attachments] }
          : createComposeModel(saved);
        this.state = newerChangesExist ? 'dirty' : 'saved';
        this.conflict = undefined;
        this.error = '';
      })
      .catch((value: unknown) => {
        const error = normalizeApiError(value);
        const details = error.details && typeof error.details === 'object'
          ? error.details as Record<string, unknown>
          : undefined;
        const current = details?.current;
        if (error.status === 409 && current && typeof current === 'object') {
          this.state = 'conflict';
          this.conflict = {
            local: createComposeModel(this.model),
            remote: createComposeModel(current as DraftRecord),
          };
        } else {
          this.state = 'failed';
          this.error = error.message;
        }
        throw value;
      })
      .finally(() => {
        if (this.inFlight === request) this.inFlight = undefined;
        if (this.state === 'dirty' && this.dirtyGeneration > this.savedGeneration) {
          void this.flush().catch(() => undefined);
        }
      });
    this.inFlight = request;
    return request;
  }

  async waitForIdle(): Promise<void> {
    while (this.inFlight || this.dirtyGeneration > this.savedGeneration) {
      if (this.inFlight) await this.inFlight.catch(() => undefined);
      else await this.flush().catch(() => undefined);
      if (this.state === 'conflict' || this.state === 'failed') return;
    }
  }

  resolveConflict(choice: 'local' | 'remote'): void {
    if (!this.conflict) return;
    this.model = createComposeModel(choice === 'remote' ? this.conflict.remote : {
      ...this.conflict.local,
      version: this.conflict.remote.version,
    });
    this.conflict = undefined;
    if (choice === 'remote') {
      this.savedGeneration = this.dirtyGeneration;
      this.state = 'saved';
    } else {
      this.dirtyGeneration += 1;
      this.state = 'dirty';
      this.schedule();
    }
  }

  hasUnsavedChanges(): boolean {
    return this.dirtyGeneration > this.savedGeneration || this.state === 'saving';
  }

  destroy(): void {
    if (this.timer) this.clearTimeoutFn(this.timer);
    this.timer = undefined;
  }

  private schedule(): void {
    if (this.timer) this.clearTimeoutFn(this.timer);
    this.timer = this.setTimeoutFn(() => {
      this.timer = undefined;
      void this.flush().catch(() => undefined);
    }, this.debounceMs);
  }
}
