import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LibraryIE from './LibraryIE';

const operationsModule = vi.hoisted(() => ({
  closeIeDoor: vi.fn<() => Promise<void>>(),
  getExportStatus: vi.fn<() => Promise<{ state: string }>>(),
  getImportStatus: vi.fn<() => Promise<{ state: string }>>(),
  listIeStations: vi.fn<
    () => Promise<
      Array<{
        id: string;
        serialNumber: string;
        state: string;
        status: string;
        slotCount: number;
        slots: Array<{ id: string; address: string; type: string; state: string; barcode: string | null }>;
      }>
    >
  >(),
  openIeDoor: vi.fn<() => Promise<void>>(),
  startExport: vi.fn<() => Promise<{ jobId: string }>>(),
  startImport: vi.fn<() => Promise<{ jobId: string }>>(),
}));

vi.mock('../api/operations', () => operationsModule);
vi.mock('../lib/useLibraryScope', () => ({
  useLibraryScope: () => ({
    libraryId: 'library-a',
    libraryName: 'Quantum i3',
    isAll: false,
    setLibrary: vi.fn(),
  }),
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LibraryIE />
    </QueryClientProvider>,
  );
}

describe('LibraryIE', () => {
  beforeEach(() => {
    operationsModule.closeIeDoor.mockReset();
    operationsModule.getExportStatus.mockReset();
    operationsModule.getImportStatus.mockReset();
    operationsModule.listIeStations.mockReset();
    operationsModule.openIeDoor.mockReset();
    operationsModule.startExport.mockReset();
    operationsModule.startImport.mockReset();

    operationsModule.getImportStatus.mockResolvedValue({ state: 'idle' });
    operationsModule.getExportStatus.mockResolvedValue({ state: 'idle' });
    operationsModule.openIeDoor.mockResolvedValue();
    operationsModule.closeIeDoor.mockResolvedValue();
    operationsModule.startImport.mockResolvedValue({ jobId: 'import-job-1' });
    operationsModule.startExport.mockResolvedValue({ jobId: 'export-job-1' });
    operationsModule.listIeStations.mockResolvedValue([
      {
        id: 'IE-1',
        serialNumber: 'SN-IE-1',
        state: 'closed',
        status: 'ready',
        slotCount: 2,
        slots: [
          { id: 'slot-1', address: 'A1', type: 'mail-slot', state: 'loaded', barcode: 'TAPE001' },
          { id: 'slot-2', address: 'A2', type: 'mail-slot', state: 'empty', barcode: null },
        ],
      },
      {
        id: 'IE-2',
        serialNumber: 'SN-IE-2',
        state: 'open',
        status: 'ready',
        slotCount: 1,
        slots: [{ id: 'slot-3', address: 'B1', type: 'mail-slot', state: 'loaded', barcode: 'TAPE002' }],
      },
    ]);
  });

  it('renders IE station details for the active library', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('IE Station')).toBeTruthy();
    });

    expect(screen.getByText(/Quantum i3/)).toBeTruthy();
    expect(screen.getAllByText('IE-1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('SN-IE-1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('TAPE001').length).toBeGreaterThan(0);
  });

  it('starts guarded import and export operations for the selected station', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('IE-2')).toBeTruthy();
    });

    fireEvent.click(screen.getByText('IE-2').closest('button')!);
    fireEvent.click(screen.getByRole('button', { name: 'Start Import' }));
    fireEvent.click(screen.getByRole('button', { name: 'Export Loaded Media' }));

    await waitFor(() => {
      expect(operationsModule.startImport).toHaveBeenCalledWith('IE-2');
    });
    expect(operationsModule.startExport).toHaveBeenCalledWith('IE-2', 'TAPE002');
  });
});
