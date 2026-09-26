import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TreeRestore from './TreeRestore';
import { ApiError } from '../api/client';
import type { TreeRestoreResult } from '../api/treeRestore';

const treeRestoreModule = vi.hoisted(() => ({
  runTreeRestore: vi.fn<
    (request: { catalogPrefix: string; destDir: string; dryRun: boolean }) => Promise<TreeRestoreResult>
  >(),
  listCatalogPrefixes: vi.fn<() => Promise<string[]>>(),
}));

vi.mock('../api/treeRestore', () => treeRestoreModule);

function result(overrides: Partial<TreeRestoreResult> = {}): TreeRestoreResult {
  return {
    jobId: 'job-1',
    catalogPrefix: '/photo-archive',
    destDir: '/restore/photos',
    dryRun: true,
    filesRestored: 4,
    filesFailed: 0,
    bytesRestored: 2048,
    filesVerified: 4,
    perTapeCounts: { PHO001L8: 3, PHO002L8: 1 },
    tapesUsed: ['PHO001L8', 'PHO002L8'],
    failures: [],
    filesSkipped: 0,
    skippedPaths: [],
    status: 'completed',
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <TreeRestore />
    </QueryClientProvider>,
  );
}

async function fillForm() {
  await waitFor(() => {
    expect(screen.getByLabelText('Catalog prefix')).toBeTruthy();
  });
  fireEvent.change(screen.getByLabelText('Catalog prefix'), {
    target: { value: '/photo-archive' },
  });
  fireEvent.change(screen.getByLabelText('Destination directory'), {
    target: { value: '/restore/photos' },
  });
}

describe('TreeRestore', () => {
  beforeEach(() => {
    treeRestoreModule.runTreeRestore.mockReset();
    treeRestoreModule.listCatalogPrefixes.mockReset();
    treeRestoreModule.listCatalogPrefixes.mockResolvedValue(['/photo-archive', '/photo-archive/2020']);
    treeRestoreModule.runTreeRestore.mockResolvedValue(result());
  });

  it('offers catalog prefixes from the catalog listing', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '/photo-archive/2020' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: '/photo-archive/2020' }));
    expect((screen.getByLabelText('Catalog prefix') as HTMLInputElement).value).toBe(
      '/photo-archive/2020',
    );
  });

  it('requires a dry-run before the restore can be run', async () => {
    renderPage();
    await fillForm();

    expect((screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.getByText(/Run the dry-run preview first/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));

    await waitFor(() => {
      expect(treeRestoreModule.runTreeRestore).toHaveBeenCalledWith({
        catalogPrefix: '/photo-archive',
        destDir: '/restore/photos',
        dryRun: true,
      });
    });
    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled,
      ).toBe(false);
    });
  });

  it('shows the dry-run plan with per-tape counts', async () => {
    renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));

    await waitFor(() => {
      expect(screen.getByText('Dry-run preview')).toBeTruthy();
    });
    expect(screen.getByText('Files planned')).toBeTruthy();
    expect(screen.getByText('Planned')).toBeTruthy();
    expect(screen.getByText('/photo-archive → /restore/photos')).toBeTruthy();
    expect(screen.getByText(/PHO001L8/)).toBeTruthy();
    expect(screen.getByText(/3 file\(s\)/)).toBeTruthy();
  });

  it('runs the restore after the plan and reports the outcome', async () => {
    renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));
    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled,
      ).toBe(false);
    });

    treeRestoreModule.runTreeRestore.mockResolvedValue(
      result({ dryRun: false, filesRestored: 4, status: 'completed' }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run restore' }));

    await waitFor(() => {
      expect(treeRestoreModule.runTreeRestore).toHaveBeenLastCalledWith({
        catalogPrefix: '/photo-archive',
        destDir: '/restore/photos',
        dryRun: false,
      });
    });
    await waitFor(() => {
      expect(screen.getByText('Files restored')).toBeTruthy();
    });
    expect(screen.getByText('completed')).toBeTruthy();
  });

  it('names skipped and failed files rather than reporting a clean run', async () => {
    treeRestoreModule.runTreeRestore.mockResolvedValue(
      result({
        dryRun: false,
        filesRestored: 1,
        filesFailed: 1,
        failures: [{ catalogPath: '/photo-archive/b.raw', error: 'ChecksumMismatchError' }],
        filesSkipped: 1,
        skippedPaths: ['/photo-archive/never-archived.raw'],
        status: 'failed',
      }),
    );
    renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));

    await waitFor(() => {
      expect(screen.getByText(/1 catalogued file\(s\) had nothing archived/)).toBeTruthy();
    });
    expect(screen.getByText('/photo-archive/never-archived.raw')).toBeTruthy();
    expect(screen.getByText('1 file(s) failed')).toBeTruthy();
    expect(screen.getByText(/ChecksumMismatchError/)).toBeTruthy();
  });

  it('clears a finished run so one more click cannot repeat it', async () => {
    renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));
    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled,
      ).toBe(false);
    });

    treeRestoreModule.runTreeRestore.mockResolvedValue(result({ dryRun: false }));
    fireEvent.click(screen.getByRole('button', { name: 'Run restore' }));
    await waitFor(() => {
      expect(screen.getByText('Files restored')).toBeTruthy();
    });

    // A restore that already happened must not be one click from happening again.
    expect((screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(treeRestoreModule.runTreeRestore).toHaveBeenCalledTimes(2);
  });

  it('drops a stale result when the selection changes', async () => {
    renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));
    await waitFor(() => {
      expect(screen.getByText('/photo-archive → /restore/photos')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Destination directory'), {
      target: { value: '/restore/elsewhere' },
    });
    // The panel answered for the old destination; keeping it on screen next to a
    // changed form is how someone restores to the wrong place.
    expect(screen.queryByText('/photo-archive → /restore/photos')).toBeNull();
    expect((screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('renders a failed dry-run as an error and leaves the run gated', async () => {
    treeRestoreModule.runTreeRestore.mockRejectedValue(
      new ApiError('Job failed (CommandError); see server logs for detail', 500, 'impact', 'action'),
    );
    renderPage();
    await fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Dry-run preview' }));

    await waitFor(() => {
      expect(screen.getAllByText(/Job failed \(CommandError\)/).length).toBeGreaterThan(0);
    });
    expect((screen.getByRole('button', { name: 'Run restore' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('still lets a prefix be typed when the catalog listing fails', async () => {
    treeRestoreModule.listCatalogPrefixes.mockRejectedValue(new Error('catalog unavailable'));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/could not be loaded; type a prefix instead/)).toBeTruthy();
    });
  });
});
