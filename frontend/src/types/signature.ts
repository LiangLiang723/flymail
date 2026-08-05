export type SignatureEntrySource = 'compose' | 'settings' | 'menu';
export type ComposeKind = 'new' | 'reply' | 'forward' | 'draft';

export interface SignatureTemplate {
  id: number;
  name: string;
  content_html: string;
  account_id: string;
  is_default: boolean;
  is_reply_default: boolean;
}

export interface SignatureDraft {
  id: number | null;
  name: string;
  content_html: string;
  account_id: string;
  is_default: boolean;
  is_reply_default: boolean;
}
