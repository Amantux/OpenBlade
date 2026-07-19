import { rootApiRequest } from './client';

export interface AssistantStatus {
  enabled: boolean;
  configured: boolean;
  model: string;
  endpoint: string;
  tools: string[];
}

export interface AssistantChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AssistantChatResponse {
  reply: string;
  tools_used: string[];
  model: string;
}

export function getAssistantStatus(): Promise<AssistantStatus> {
  return rootApiRequest<AssistantStatus>('/assistant/status');
}

export function sendAssistantChat(
  messages: AssistantChatMessage[],
): Promise<AssistantChatResponse> {
  return rootApiRequest<AssistantChatResponse>('/assistant/chat', {
    method: 'POST',
    body: { messages },
  });
}
