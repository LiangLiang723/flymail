import type {
  AttachmentSummary,
  BodyResponse,
  MessageSummary,
  ThreadDetailResponse,
} from '../../shared/api/generated.ts';

export type { AttachmentSummary, BodyResponse, MessageSummary, ThreadDetailResponse };

export interface SanitizedMailBody {
  html: string;
  blockedRemoteImages: string[];
}

export interface MessageBodyViewState {
  loading: boolean;
  response?: BodyResponse;
  error?: string;
  allowRemoteImages: boolean;
}
