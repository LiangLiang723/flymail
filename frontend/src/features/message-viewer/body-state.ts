import type { BodyResponse, BodyState } from '../../shared/api/generated.ts';

export function bodyStateMessage(state: BodyState): string {
  const messages: Record<BodyState, string> = {
    not_requested: '正文尚未请求。',
    queued: '正文获取任务已排队。',
    fetching: '正在从邮箱获取正文。',
    ready: '正文已就绪。',
    evicted: '本地正文已释放，可重新获取。',
    failed: '正文获取失败，请重试。',
    unavailable: '账号当前无法连接，请重新连接账号后重试。',
  };
  return messages[state];
}

export class BodyRequestRegistry {
  private readonly requester: (messageId: string) => Promise<BodyResponse>;
  private readonly inFlight = new Map<string, Promise<BodyResponse>>();

  constructor(requester: (messageId: string) => Promise<BodyResponse>) {
    this.requester = requester;
  }

  request(messageId: string): Promise<BodyResponse> {
    const existing = this.inFlight.get(messageId);
    if (existing) return existing;
    const request = this.requester(messageId).finally(() => {
      if (this.inFlight.get(messageId) === request) this.inFlight.delete(messageId);
    });
    this.inFlight.set(messageId, request);
    return request;
  }

  clear(messageId?: string): void {
    if (messageId) this.inFlight.delete(messageId);
    else this.inFlight.clear();
  }
}
