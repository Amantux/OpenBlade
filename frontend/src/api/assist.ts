import { rootApiRequest } from './client';

export type AssistRole = 'user' | 'assistant';

export interface AssistMessage {
  role: AssistRole;
  content: string;
}

export interface AssistReply {
  reply: string;
  /** Tool names only — a trace of what the assistant consulted, never arguments. */
  toolCalls: string[];
}

/** Conversation caps enforced by the API (docs/wiki/guides/assist-api.md). */
export const ASSIST_MAX_MESSAGE_CHARS = 8000;

/**
 * Ask one question. The whole conversation travels with every request: the
 * endpoint is stateless and request/response — there is no streaming and no
 * session id to hold on to.
 *
 * The HTTP surface is read-only by construction; it cannot create, format or
 * erase anything, so nothing here needs a confirmation flow.
 */
export function askAssistant(messages: AssistMessage[]): Promise<AssistReply> {
  return rootApiRequest<AssistReply>('/assist', {
    method: 'POST',
    body: { messages },
  });
}
