import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Assistant from './Assistant';
import { ApiError } from '../api/client';
import type { AssistMessage, AssistReply } from '../api/assist';

const assistModule = vi.hoisted(() => ({
  askAssistant: vi.fn<(messages: AssistMessage[]) => Promise<AssistReply>>(),
  ASSIST_MAX_MESSAGE_CHARS: 8000,
}));

vi.mock('../api/assist', () => assistModule);

const NOT_CONFIGURED_DETAIL =
  'The OpenBlade assistant is not configured. Set OPENBLADE_OLLAMA_URL to an Ollama endpoint.';

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Assistant />
    </QueryClientProvider>,
  );
}

describe('Assistant', () => {
  beforeEach(() => {
    assistModule.askAssistant.mockReset();
    assistModule.askAssistant.mockResolvedValue({
      reply: 'Two: PH000001 and PH000002.',
      toolCalls: ['get_inventory', 'list_volume_groups'],
    });
  });

  it('shows domain suggestions before anything has been asked', async () => {
    renderPage();

    expect(screen.getByText('Ask about the library')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Why did my last restore fail\?/ })).toBeTruthy();
  });

  it('sends the question and renders the reply with a dim tool trace', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'how many tapes are in photo-archive?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    await waitFor(() => {
      expect(screen.getByText('Two: PH000001 and PH000002.')).toBeTruthy();
    });
    expect(assistModule.askAssistant).toHaveBeenCalledWith([
      { role: 'user', content: 'how many tapes are in photo-archive?' },
    ]);
    expect(screen.getByText('consulted get_inventory, list_volume_groups')).toBeTruthy();
    expect(screen.getByText('how many tapes are in photo-archive?')).toBeTruthy();
  });

  it('sends the whole conversation on the next turn — the endpoint is stateless', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'first question' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
    await waitFor(() => {
      expect(screen.getByText('Two: PH000001 and PH000002.')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'second question' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    await waitFor(() => {
      expect(assistModule.askAssistant).toHaveBeenLastCalledWith([
        { role: 'user', content: 'first question' },
        { role: 'assistant', content: 'Two: PH000001 and PH000002.' },
        { role: 'user', content: 'second question' },
      ]);
    });
  });

  it('asks a suggestion straight from the empty state', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /Why did my last restore fail\?/ }));

    await waitFor(() => {
      expect(assistModule.askAssistant).toHaveBeenCalledWith([
        { role: 'user', content: 'Why did my last restore fail?' },
      ]);
    });
  });

  it('renders a 503 as setup instructions and hands the question back', async () => {
    assistModule.askAssistant.mockRejectedValue(
      new ApiError(NOT_CONFIGURED_DETAIL, 503, 'impact', 'action'),
    );
    renderPage();

    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'anything' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    await waitFor(() => {
      expect(screen.getByText('The assistant is not configured')).toBeTruthy();
    });
    expect(screen.getByText(/OPENBLADE_OLLAMA_URL=http:\/\/localhost:11434/)).toBeTruthy();
    // The orphaned user turn is popped and the text returned to the composer, so
    // the thread never shows a question that nothing answered.
    expect(screen.queryByText('anything')).toBeNull();
    expect((screen.getByLabelText('Question') as HTMLInputElement).value).toBe('anything');
    expect(screen.getByText('Ask about the library')).toBeTruthy();
  });

  it('renders an upstream failure as an error, keeping the thread', async () => {
    assistModule.askAssistant.mockRejectedValue(
      new ApiError('The model endpoint could not be reached', 502, 'impact', 'action'),
    );
    renderPage();

    fireEvent.change(screen.getByLabelText('Question'), { target: { value: 'why is it slow?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    await waitFor(() => {
      expect(screen.getAllByText(/The model endpoint could not be reached/).length).toBeGreaterThan(
        0,
      );
    });
    expect(screen.queryByText('The assistant is not configured')).toBeNull();
    expect(screen.getByText('why is it slow?')).toBeTruthy();
  });
});
