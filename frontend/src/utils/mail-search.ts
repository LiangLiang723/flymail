import type { MailSearchState } from '../types/mail';

export function createEmptyMailSearch(): MailSearchState {
  return {
    keyword: '',
    fromAddr: '',
    toAddr: '',
    subject: '',
    body: '',
    after: '',
    before: '',
    readFilter: '',
    attachmentOnly: false,
    starredOnly: false,
  };
}

export function hasMailSearchFilters(state: MailSearchState): boolean {
  return Boolean(
    state.keyword.trim()
      || state.fromAddr.trim()
      || state.toAddr.trim()
      || state.subject.trim()
      || state.body.trim()
      || state.after.trim()
      || state.before.trim()
      || state.readFilter
      || state.attachmentOnly
      || state.starredOnly,
  );
}

export function serializeMailSearchParams(state: MailSearchState): Record<string, string | boolean> {
  const params: Record<string, string | boolean> = {};
  if (state.keyword.trim()) params.keyword = state.keyword.trim();
  if (state.fromAddr.trim()) params.from_addr = state.fromAddr.trim();
  if (state.toAddr.trim()) params.to_addr = state.toAddr.trim();
  if (state.subject.trim()) params.subject = state.subject.trim();
  if (state.body.trim()) params.body = state.body.trim();
  if (state.after.trim()) params.after = state.after.trim();
  if (state.before.trim()) params.before = state.before.trim();
  if (state.readFilter) params.read_filter = state.readFilter;
  if (state.attachmentOnly) params.attachment_filter = true;
  if (state.starredOnly) params.starred_filter = true;
  return params;
}
