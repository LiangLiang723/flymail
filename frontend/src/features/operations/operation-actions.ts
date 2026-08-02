import type { OperationAccepted, OperationCommand } from '../../entities/operation/types.ts';
import type { ThreadProjection } from '../../shared/api/generated.ts';

interface AdapterOptions {
  submit: (command: OperationCommand & { idempotency_key: string }) => Promise<OperationAccepted>;
  patchProjection: (targetId: string, projection: ThreadProjection) => void;
  fetchAuthoritativeProjection: (targetId: string) => Promise<ThreadProjection>;
  idempotencyKey?: () => string;
}

function stable(value: unknown): string {
  if (!value || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  return `{${Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${stable(item)}`).join(',')}}`;
}

export class OperationCommandAdapter {
  private readonly options: AdapterOptions;
  private readonly inFlight = new Map<string, Promise<OperationAccepted>>();

  constructor(options: AdapterOptions) {
    this.options = options;
  }

  execute(command: OperationCommand): Promise<OperationAccepted> {
    const key = `${command.target_type}:${command.target_id}:${command.operation_type}:${stable(command.desired_state)}`;
    const existing = this.inFlight.get(key);
    if (existing) return existing;
    const idempotencyKey = this.options.idempotencyKey?.() || crypto.randomUUID();
    const request = this.options.submit({ ...command, idempotency_key: idempotencyKey })
      .then((result) => {
        if (result.projection) this.options.patchProjection(command.target_id, result.projection);
        return result;
      })
      .catch(async (error: unknown) => {
        const authoritative = await this.options.fetchAuthoritativeProjection(command.target_id);
        this.options.patchProjection(command.target_id, authoritative);
        throw error;
      })
      .finally(() => {
        if (this.inFlight.get(key) === request) this.inFlight.delete(key);
      });
    this.inFlight.set(key, request);
    return request;
  }
}

export function undoRemainingMs(expiresAt: number, now = Date.now()): number {
  const normalized = expiresAt < 10_000_000_000 && now >= 10_000_000_000
    ? expiresAt * 1000
    : expiresAt;
  return Math.max(0, normalized - now);
}

export function canPermanentlyDelete(targetName: string, typedName: string): boolean {
  return targetName.length > 0 && targetName === typedName;
}

export function conflictResolutions(kind: string): string[] {
  const supported: Record<string, string[]> = {
    draft_version: ['keep_local', 'keep_remote', 'save_copy'],
    uncertain_send: ['mark_sent', 'retry', 'cancel'],
    missing_mailbox: ['choose_mailbox', 'cancel'],
    operation_conflict: ['retry', 'cancel'],
  };
  return supported[kind] ? [...supported[kind]] : [];
}
