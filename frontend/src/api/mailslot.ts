import { rootApiRequest } from './client';

export interface MailslotSlot {
  slotId: number;
  occupied: boolean;
  barcode: string | null;
}

export interface MailslotListing {
  importExportSlots: MailslotSlot[];
  slotCount: number;
  occupiedCount: number;
  /** False when this backend has no import/export station at all. */
  supported: boolean;
}

/** What would leave the library with a cartridge. Returned by the preview call. */
export interface ExportAssessment {
  barcode: string;
  volumeGroup: string | null;
  archivedFilesOnCartridge: number;
  pendingFilesOnCartridge: number;
  bytesOnCartridge: number;
  samplePaths: string[];
  volumeGroupBarcodesWithData: string[];
  carriesData: boolean;
}

export interface MailslotMoveResult {
  opId: string;
  barcode: string;
  source: string;
  sourceSlot: number;
  destination: string;
  destinationSlot: number;
  /** True when the backend picked the destination slot rather than the operator. */
  destinationSlotChosen: boolean;
  exported?: ExportAssessment | null;
}

export function listMailslotSlots(): Promise<MailslotListing> {
  return rootApiRequest<MailslotListing>('/mailslot/slots');
}

export function previewMailslotExport(barcode: string): Promise<ExportAssessment> {
  return rootApiRequest<ExportAssessment>(`/mailslot/export-preview/${encodeURIComponent(barcode)}`);
}

export function importFromMailslot(ieSlot: number, toSlot?: number): Promise<MailslotMoveResult> {
  return rootApiRequest<MailslotMoveResult>('/mailslot/import', {
    method: 'POST',
    body: { ie_slot: ieSlot, to_slot: toSlot ?? null },
  });
}

/**
 * Export a cartridge out of the library.
 *
 * The API answers 409 with the refusal message when the cartridge still carries
 * archived data; a typed `confirmBarcode` equal to the cartridge is the only way
 * past it and must never be sent without
 * an explicit operator confirmation (see ExportConfirmDialog).
 */
export function exportThroughMailslot(
  barcode: string,
  options: { ieSlot?: number; confirmBarcode?: string } = {},
): Promise<MailslotMoveResult> {
  return rootApiRequest<MailslotMoveResult>('/mailslot/export', {
    method: 'POST',
    body: {
      barcode,
      ie_slot: options.ieSlot ?? null,
      ...(options.confirmBarcode ? { confirmBarcode: options.confirmBarcode } : {}),
    },
  });
}
