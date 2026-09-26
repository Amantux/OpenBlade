import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Mailslot from './Mailslot';
import { ApiError } from '../api/client';
import type { ExportAssessment, MailslotListing, MailslotMoveResult } from '../api/mailslot';

const mailslotModule = vi.hoisted(() => ({
  listMailslotSlots: vi.fn<() => Promise<MailslotListing>>(),
  previewMailslotExport: vi.fn<(barcode: string) => Promise<ExportAssessment>>(),
  importFromMailslot: vi.fn<(ieSlot: number, toSlot?: number) => Promise<MailslotMoveResult>>(),
  exportThroughMailslot: vi.fn<
    (barcode: string, options?: { ieSlot?: number; confirmBarcode?: string }) => Promise<MailslotMoveResult>
  >(),
}));

vi.mock('../api/mailslot', () => mailslotModule);

const OCCUPIED_LISTING: MailslotListing = {
  importExportSlots: [
    { slotId: 51, occupied: true, barcode: 'PHO001L8' },
    { slotId: 52, occupied: false, barcode: null },
  ],
  slotCount: 2,
  occupiedCount: 1,
  supported: true,
};

const CARRIES_DATA: ExportAssessment = {
  barcode: 'ARC001L8',
  volumeGroup: 'photo-archive',
  archivedFilesOnCartridge: 3,
  pendingFilesOnCartridge: 1,
  bytesOnCartridge: 4096,
  samplePaths: ['/photo-archive/a.raw', '/photo-archive/b.raw'],
  volumeGroupBarcodesWithData: ['ARC002L8'],
  carriesData: true,
};

const EMPTY_CARTRIDGE: ExportAssessment = {
  barcode: 'SCR001L8',
  volumeGroup: null,
  archivedFilesOnCartridge: 0,
  pendingFilesOnCartridge: 0,
  bytesOnCartridge: 0,
  samplePaths: [],
  volumeGroupBarcodesWithData: [],
  carriesData: false,
};

function moveResult(overrides: Partial<MailslotMoveResult> = {}): MailslotMoveResult {
  return {
    opId: 'op-1',
    barcode: 'PHO001L8',
    source: 'import_export_slot',
    sourceSlot: 51,
    destination: 'storage_slot',
    destinationSlot: 7,
    destinationSlotChosen: true,
    ...overrides,
  };
}

function refusal(): ApiError {
  return new ApiError(
    'Cartridge ARC001L8 still carries archived data: 3 file instance(s), 4096 bytes; '
      + 'volume group photo-archive; e.g. /photo-archive/a.raw. Exporting makes these '
      + 'unrestorable until the cartridge is imported again. Re-run with --confirm-barcode if that is what you mean.',
    409,
    'The backend could not complete POST /mailslot/export.',
    'Check the appliance state, then retry the request.',
  );
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Mailslot />
    </QueryClientProvider>,
  );
}

describe('Mailslot', () => {
  beforeEach(() => {
    mailslotModule.listMailslotSlots.mockReset();
    mailslotModule.previewMailslotExport.mockReset();
    mailslotModule.importFromMailslot.mockReset();
    mailslotModule.exportThroughMailslot.mockReset();

    mailslotModule.listMailslotSlots.mockResolvedValue(OCCUPIED_LISTING);
    mailslotModule.previewMailslotExport.mockResolvedValue(CARRIES_DATA);
    mailslotModule.importFromMailslot.mockResolvedValue(moveResult());
    mailslotModule.exportThroughMailslot.mockResolvedValue(
      moveResult({
        barcode: 'SCR001L8',
        source: 'storage_slot',
        sourceSlot: 4,
        destination: 'import_export_slot',
        destinationSlot: 52,
      }),
    );
  });

  it('lists import/export elements with their occupancy and barcodes', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('I/E slot 51')).toBeTruthy();
    });
    expect(screen.getByText('PHO001L8')).toBeTruthy();
    expect(screen.getByText('I/E slot 52')).toBeTruthy();
    expect(screen.getByText('Occupied')).toBeTruthy();
    expect(screen.getByText('Empty')).toBeTruthy();
    expect(screen.getByText('1 of 2 occupied')).toBeTruthy();
  });

  it('says so when the backend has no import/export station at all', async () => {
    mailslotModule.listMailslotSlots.mockResolvedValue({
      importExportSlots: [],
      slotCount: 0,
      occupiedCount: 0,
      supported: false,
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/no import\/export station/i)).toBeTruthy();
    });
    expect(screen.getByText(/No I\/E element holds a cartridge/i)).toBeTruthy();
  });

  it('imports from a chosen I/E element, letting the backend pick the storage slot', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Source I/E element')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Source I/E element'), { target: { value: '51' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import cartridge' }));

    await waitFor(() => {
      expect(mailslotModule.importFromMailslot).toHaveBeenCalledWith(51, undefined);
    });
    expect(screen.getByText(/Imported PHO001L8/)).toBeTruthy();
    expect(screen.getByText(/slot chosen automatically/)).toBeTruthy();
  });

  it('imports into an explicit target slot when one is given', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Source I/E element')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Source I/E element'), { target: { value: '51' } });
    fireEvent.click(screen.getByLabelText('Target slot'));
    fireEvent.change(screen.getByLabelText('Target storage slot'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import cartridge' }));

    await waitFor(() => {
      expect(mailslotModule.importFromMailslot).toHaveBeenCalledWith(51, 12);
    });
  });

  it('previews what would leave with a cartridge before anything moves', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check what would leave' }));

    await waitFor(() => {
      expect(screen.getByText(/still carries archived data/)).toBeTruthy();
    });
    expect(screen.getByText('3 archived file instance(s)')).toBeTruthy();
    expect(screen.getByText('1 write(s) still in flight to it')).toBeTruthy();
    expect(screen.getByText('Volume group photo-archive')).toBeTruthy();
    expect(screen.getAllByText('/photo-archive/a.raw').length).toBeGreaterThan(0);
    expect(mailslotModule.exportThroughMailslot).not.toHaveBeenCalled();
  });

  it('drops the assessment when the barcode field changes', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check what would leave' }));
    await waitFor(() => {
      expect(screen.getByText(/still carries archived data/)).toBeTruthy();
    });

    // A panel describing a different cartridge is worse than no panel.
    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'SCR001L8' },
    });
    expect(screen.queryByText(/still carries archived data/)).toBeNull();
  });

  it('exports a cartridge that carries nothing without asking for confirmation', async () => {
    mailslotModule.previewMailslotExport.mockResolvedValue(EMPTY_CARTRIDGE);
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'SCR001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export cartridge' }));

    await waitFor(() => {
      expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledWith('SCR001L8', {
      });
    });
    expect(screen.getByText(/Exported SCR001L8/)).toBeTruthy();
  });

  it('surfaces the refusal naming what is on the cartridge, and never forces on its own', async () => {
    mailslotModule.exportThroughMailslot.mockRejectedValue(refusal());
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export cartridge' }));

    await waitFor(() => {
      expect(screen.getByText('Export refused')).toBeTruthy();
    });
    expect(screen.getByText(/still carries archived data: 3 file instance\(s\)/)).toBeTruthy();
    expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledTimes(1);
    expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledWith('ARC001L8', {});
  });

  it('gates the forced export behind the typed barcode', async () => {
    mailslotModule.exportThroughMailslot.mockRejectedValue(refusal());
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export cartridge' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Force export…' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Force export…' }));

    const confirmButton = await waitFor(() =>
      screen.getByRole('button', { name: 'Export anyway' }),
    );
    // Nothing typed: the gate is shut.
    expect((confirmButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(confirmButton);
    expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledTimes(1);

    // A near miss is still a miss.
    const typedBarcode = screen.getByLabelText(/Type the barcode/);
    fireEvent.change(typedBarcode, { target: { value: 'ARC001L' } });
    expect((screen.getByRole('button', { name: 'Export anyway' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Export anyway' }));
    expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledTimes(1);

    // The exact barcode, and only then, opens it.
    fireEvent.change(typedBarcode, { target: { value: 'ARC001L8' } });
    expect((screen.getByRole('button', { name: 'Export anyway' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Export anyway' }));

    await waitFor(() => {
      expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledTimes(2);
    });
    expect(mailslotModule.exportThroughMailslot).toHaveBeenLastCalledWith('ARC001L8', {
      confirmBarcode: 'ARC001L8',
    });
  });

  it('shows what leaves with the cartridge inside the force dialog', async () => {
    mailslotModule.exportThroughMailslot.mockRejectedValue(refusal());
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export cartridge' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Force export…' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Force export…' }));

    await waitFor(() => {
      expect(screen.getByText('Force export of ARC001L8?')).toBeTruthy();
    });
    expect(screen.getByText('Files that leave with it')).toBeTruthy();
    expect(screen.getByText('Archived file instances')).toBeTruthy();
    expect(screen.getAllByText(/ARC002L8/).length).toBeGreaterThan(0);
  });

  it('drops the force affordance when the barcode changes after a refusal', async () => {
    mailslotModule.exportThroughMailslot.mockRejectedValue(refusal());
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export cartridge' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Force export…' })).toBeTruthy();
    });

    // The refusal was about ARC001L8. Editing the field must not leave a force
    // button armed that would then export a cartridge nobody assessed.
    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'SCR002L8' },
    });
    expect(screen.queryByRole('button', { name: 'Force export…' })).toBeNull();
    expect(screen.queryByText('Export refused')).toBeNull();
  });

  it('refuses to confirm a force when what is on the cartridge cannot be read', async () => {
    mailslotModule.exportThroughMailslot.mockRejectedValue(refusal());
    mailslotModule.previewMailslotExport.mockRejectedValue(
      new ApiError('catalog unavailable', 500, 'impact', 'action'),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByLabelText('Cartridge barcode')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Cartridge barcode'), {
      target: { value: 'ARC001L8' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Export cartridge' }));
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Force export…' })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Force export…' }));

    await waitFor(() => {
      expect(screen.getByText('Cannot read what is on ARC001L8')).toBeTruthy();
    });
    // Right barcode typed, but there is no assessment to have consented to.
    fireEvent.change(screen.getByLabelText(/Type the barcode/), {
      target: { value: 'ARC001L8' },
    });
    const confirmButton = screen.getByRole('button', { name: 'Export anyway' }) as HTMLButtonElement;
    expect(confirmButton.disabled).toBe(true);
    fireEvent.click(confirmButton);
    expect(mailslotModule.exportThroughMailslot).toHaveBeenCalledTimes(1);
    expect(mailslotModule.exportThroughMailslot).not.toHaveBeenCalledWith('ARC001L8', {
      confirmBarcode: 'ARC001L8',
    });
  });

  it('renders a load failure with a retry instead of an empty station', async () => {
    mailslotModule.listMailslotSlots.mockRejectedValue(new Error('backend down'));
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy();
    });
  });
});
