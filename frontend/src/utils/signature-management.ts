import type {
  ComposeKind,
  SignatureDraft,
  SignatureTemplate,
} from '../types/signature';

export function createEmptySignatureDraft(accountId = ''): SignatureDraft {
  return {
    id: null,
    name: '',
    content_html: '<p><br></p>',
    account_id: accountId,
    is_default: false,
    is_reply_default: false,
  };
}

export function createSignatureDraft(signature: SignatureTemplate): SignatureDraft {
  return {
    id: signature.id,
    name: signature.name,
    content_html: signature.content_html,
    account_id: signature.account_id,
    is_default: signature.is_default,
    is_reply_default: signature.is_reply_default,
  };
}

export function duplicateSignatureDraft(signature: SignatureTemplate): SignatureDraft {
  return {
    id: null,
    name: `${signature.name} - 副本`,
    content_html: signature.content_html,
    account_id: signature.account_id,
    is_default: false,
    is_reply_default: false,
  };
}

export function serializeSignatureDraft(draft: SignatureDraft): string {
  return JSON.stringify({
    id: draft.id,
    name: draft.name,
    content_html: draft.content_html,
    account_id: draft.account_id,
    is_default: draft.is_default,
    is_reply_default: draft.is_reply_default,
  });
}

export function filterSignatures(
  signatures: SignatureTemplate[],
  search: string,
  accountId: string,
): SignatureTemplate[] {
  const normalizedSearch = search.trim().toLocaleLowerCase();
  return signatures.filter((signature) => {
    const searchMatches = !normalizedSearch
      || signature.name.toLocaleLowerCase().includes(normalizedSearch);
    const accountMatches = accountId === 'all'
      || signature.account_id === accountId;
    return searchMatches && accountMatches;
  });
}

export function resolveDefaultSignature(
  signatures: SignatureTemplate[],
  accountId: string,
  composeKind: ComposeKind,
): SignatureTemplate | null {
  if (composeKind === 'draft') return null;
  const useReplyDefault = composeKind === 'reply' || composeKind === 'forward';
  const defaults = signatures.filter((signature) => (
    useReplyDefault ? signature.is_reply_default : signature.is_default
  ));
  return defaults.find((signature) => signature.account_id === accountId)
    || defaults.find((signature) => !signature.account_id)
    || null;
}
