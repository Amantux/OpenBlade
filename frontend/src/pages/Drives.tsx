import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cleanDrive,
  getDrive,
  getDriveStatistics,
  getDriveStatus,
  listDriveCleaningReports,
  listDrives,
  listDrivesNeedingCleaning,
  varyOffDrive,
  varyOnDrive,
  type Drive,
} from '../api/drives';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (['FAULTED', 'FAILED', 'OFFLINE'].includes(state)) return 'red';
  if (['BUSY', 'MOUNTING', 'UNMOUNTING', 'LOADING', 'UNLOADING', 'CLEANING'].includes(state)) return 'amber';
  if (['IDLE', 'EMPTY', 'READY'].includes(state)) return 'green';
  return 'blue';
}

function getDriveId(serialNumber: string): string {
  const match = serialNumber.match(/(\d+)$/);
  return match ? `D${match[1].padStart(3, '0')}` : serialNumber;
}

function getGeneration(type: string, fallback?: Drive['generation']): string {
  if (fallback !== undefined && fallback !== null && `${fallback}`.trim()) {
    return `${fallback}`.toUpperCase().replace(/^LTO-?/, 'LTO-');
  }

  const match = type.match(/(\d+)/);
  return match ? `LTO-${match[1]}` : type || 'Unknown';
}

function getInterfaceLabel(drive: Drive): string {
  return drive.interface ?? 'FC';
}

function getBarcode(drive?: Drive): string {
  return drive?.loadedMedia?.barcode ?? 'Empty';
}

function getMountState(drive?: Drive): string {
  return drive?.loadedMedia?.barcode ? 'MOUNTED' : 'EMPTY';
}

function getOperationalState(drive?: Drive): string {
  if (!drive) {
    return 'UNKNOWN';
  }

  if (String(drive.status).toUpperCase() === 'OFFLINE') {
    return 'OFFLINE';
  }

  return String(drive.state).toUpperCase();
}

function buildMixedGenerationBanner(drives: Drive[]): string | null {
  const counts = drives.reduce<Record<string, number>>((acc, drive) => {
    const generation = getGeneration(drive.type, drive.generation);
    acc[generation] = (acc[generation] ?? 0) + 1;
    return acc;
  }, {});
  const generations = Object.entries(counts).sort(([left], [right]) => left.localeCompare(right));

  if (generations.length < 2) {
    return null;
  }

  return `Mixed LTO generations detected: ${generations.map(([generation, count]) => `${generation} (${count})`).join(', ')} — reads across generations enabled, writes use native generation`;
}

export default function Drives() {
  const queryClient = useQueryClient();
  const [selectedSerialNumber, setSelectedSerialNumber] = useState<string>();
  const [operationError, setOperationError] = useState<unknown>(null);
  const [operationMessage, setOperationMessage] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isVaryingOn, setIsVaryingOn] = useState(false);
  const [isVaryingOff, setIsVaryingOff] = useState(false);
  const [isCleaning, setIsCleaning] = useState(false);

  const drivesQuery = useQuery({ queryKey: ['drives'], queryFn: listDrives, refetchInterval: 30_000 });
  const cleaningNeedsQuery = useQuery({
    queryKey: ['drive-cleaning-needs'],
    queryFn: listDrivesNeedingCleaning,
    refetchInterval: 30_000,
  });
  const cleaningReportsQuery = useQuery({
    queryKey: ['drive-cleaning-report'],
    queryFn: listDriveCleaningReports,
    refetchInterval: 30_000,
  });

  const drives = drivesQuery.data ?? [];

  useEffect(() => {
    if (!selectedSerialNumber && drives.length > 0) {
      setSelectedSerialNumber(drives[0].serialNumber);
      return;
    }

    if (selectedSerialNumber && drives.every((drive) => drive.serialNumber !== selectedSerialNumber)) {
      setSelectedSerialNumber(drives[0]?.serialNumber);
    }
  }, [drives, selectedSerialNumber]);

  const selectedDriveFromList = drives.find((drive) => drive.serialNumber === selectedSerialNumber) ?? drives[0];
  const activeSerialNumber = selectedDriveFromList?.serialNumber;

  const driveDetailQuery = useQuery({
    queryKey: ['drive', activeSerialNumber],
    queryFn: () => getDrive(activeSerialNumber!),
    enabled: Boolean(activeSerialNumber),
  });
  const driveStatusQuery = useQuery({
    queryKey: ['drive-status', activeSerialNumber],
    queryFn: () => getDriveStatus(activeSerialNumber!),
    enabled: Boolean(activeSerialNumber),
  });
  const driveStatisticsQuery = useQuery({
    queryKey: ['drive-statistics', activeSerialNumber],
    queryFn: () => getDriveStatistics(activeSerialNumber!),
    enabled: Boolean(activeSerialNumber),
  });

  const selectedDrive = driveDetailQuery.data ?? selectedDriveFromList;
  const selectedDriveState = getOperationalState(selectedDrive);
  const selectedCleaningReport = cleaningReportsQuery.data?.find((report) => report.serialNumber === activeSerialNumber);
  const selectedNeedsCleaning = cleaningNeedsQuery.data?.some((drive) => drive.serialNumber === activeSerialNumber) ?? false;
  const mixedGenerationBanner = useMemo(() => buildMixedGenerationBanner(drives), [drives]);
  const queryError =
    drivesQuery.error ??
    cleaningNeedsQuery.error ??
    cleaningReportsQuery.error ??
    driveDetailQuery.error ??
    driveStatusQuery.error ??
    driveStatisticsQuery.error;

  function updateDriveCache(serialNumber: string, updater: (drive: Drive) => Drive) {
    queryClient.setQueryData<Drive[]>(['drives'], (current) =>
      current?.map((drive) => (drive.serialNumber === serialNumber ? updater(drive) : drive)) ?? current,
    );
    queryClient.setQueryData<Drive>(['drive', serialNumber], (current) => (current ? updater(current) : current));
  }

  async function refreshDriveData(serialNumber = activeSerialNumber) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['drives'] }),
      queryClient.invalidateQueries({ queryKey: ['drive-cleaning-needs'] }),
      queryClient.invalidateQueries({ queryKey: ['drive-cleaning-report'] }),
      serialNumber ? queryClient.invalidateQueries({ queryKey: ['drive', serialNumber] }) : Promise.resolve(),
      serialNumber ? queryClient.invalidateQueries({ queryKey: ['drive-status', serialNumber] }) : Promise.resolve(),
      serialNumber ? queryClient.invalidateQueries({ queryKey: ['drive-statistics', serialNumber] }) : Promise.resolve(),
    ]);
  }

  async function handleRefresh() {
    setIsRefreshing(true);
    setOperationError(null);

    try {
      await refreshDriveData();
      setOperationMessage('Drive inventory refreshed.');
    } catch (error) {
      setOperationError(error);
    } finally {
      setIsRefreshing(false);
    }
  }

  async function handleVaryOn() {
    if (!selectedDrive) return;

    setIsVaryingOn(true);
    setOperationError(null);
    setOperationMessage(null);
    updateDriveCache(selectedDrive.serialNumber, (drive) => ({ ...drive, status: 'online', state: 'IDLE' }));

    try {
      await varyOnDrive(selectedDrive.serialNumber);
      await refreshDriveData(selectedDrive.serialNumber);
      setOperationMessage(`Drive ${selectedDrive.serialNumber} varied online.`);
    } catch (error) {
      setOperationError(error);
      await refreshDriveData(selectedDrive.serialNumber);
    } finally {
      setIsVaryingOn(false);
    }
  }

  async function handleVaryOff() {
    if (!selectedDrive) return;

    setIsVaryingOff(true);
    setOperationError(null);
    setOperationMessage(null);
    updateDriveCache(selectedDrive.serialNumber, (drive) => ({ ...drive, status: 'offline', state: 'OFFLINE' }));

    try {
      await varyOffDrive(selectedDrive.serialNumber);
      await refreshDriveData(selectedDrive.serialNumber);
      setOperationMessage(`Drive ${selectedDrive.serialNumber} varied offline.`);
    } catch (error) {
      setOperationError(error);
      await refreshDriveData(selectedDrive.serialNumber);
    } finally {
      setIsVaryingOff(false);
    }
  }

  async function handleForceClean() {
    if (!selectedDrive || !window.confirm(`Force clean drive ${selectedDrive.serialNumber}?`)) {
      return;
    }

    setIsCleaning(true);
    setOperationError(null);
    setOperationMessage(null);
    const cleanedAt = new Date().toISOString();
    updateDriveCache(selectedDrive.serialNumber, (drive) => ({
      ...drive,
      state: 'CLEANING',
      cleaningCount: drive.cleaningCount + 1,
      lastCleaned: cleanedAt,
    }));

    try {
      await cleanDrive(selectedDrive.serialNumber);
      await refreshDriveData(selectedDrive.serialNumber);
      setOperationMessage(`Drive ${selectedDrive.serialNumber} cleaning started.`);
    } catch (error) {
      setOperationError(error);
      await refreshDriveData(selectedDrive.serialNumber);
    } finally {
      setIsCleaning(false);
    }
  }

  if (drivesQuery.isLoading || cleaningNeedsQuery.isLoading || cleaningReportsQuery.isLoading) {
    return <Spinner />;
  }
  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => void handleRefresh()} />;
  }

  return (
    <div className="space-y-4">
      {mixedGenerationBanner ? (
        <div className="rounded-md border border-sky-700 bg-sky-900/20 px-4 py-3 text-sm text-sky-100">{mixedGenerationBanner}</div>
      ) : null}

      <NorthPanel
        title="Drive Overview"
        subtitle="Operational status, mounted media, and host connectivity for each installed tape drive."
        columns={[
          { key: 'driveId', header: 'Drive ID', render: (row: Drive) => getDriveId(row.serialNumber) },
          { key: 'serialNumber', header: 'Serial Number', render: (row: Drive) => row.serialNumber },
          { key: 'type', header: 'Type', render: (row: Drive) => row.type },
          { key: 'generation', header: 'Generation', render: (row: Drive) => getGeneration(row.type, row.generation) },
          {
            key: 'state',
            header: 'State',
            render: (row: Drive) => <Badge variant={stateVariant(getOperationalState(row))}>{getOperationalState(row)}</Badge>,
          },
          { key: 'loaded', header: 'Tape Loaded', render: (row: Drive) => getBarcode(row) },
          { key: 'interface', header: 'Interface', render: (row: Drive) => getInterfaceLabel(row) },
        ]}
        rows={drives}
        getRowId={(row) => row.serialNumber}
        selectedId={activeSerialNumber}
        onSelect={(row) => setSelectedSerialNumber(row.serialNumber)}
        emptyMessage="No drives reported by the AML backend."
      />

      {selectedDrive ? (
        <InformationPanel
          title={selectedDrive.serialNumber}
          subtitle="Detailed drive state, cleaning posture, and mounted media for the selected device."
          items={[
            { label: 'SN', value: selectedDrive.serialNumber },
            { label: 'Type', value: selectedDrive.type },
            { label: 'Generation', value: getGeneration(selectedDrive.type, selectedDrive.generation) },
            { label: 'State', value: <Badge variant={stateVariant(selectedDriveState)}>{selectedDriveState}</Badge> },
            { label: 'Mount State', value: getMountState(selectedDrive) },
            { label: 'Barcode', value: getBarcode(selectedDrive) },
            { label: 'Slot Address', value: selectedDrive.location || '—' },
            { label: 'Interface', value: getInterfaceLabel(selectedDrive) },
            { label: 'Firmware Version', value: selectedDrive.firmware || '—' },
            { label: 'Cleaning Count', value: selectedDrive.cleaningCount },
            {
              label: 'Last Cleaned',
              value: selectedCleaningReport?.lastCleaned
                ? formatDate(selectedCleaningReport.lastCleaned)
                : selectedDrive.lastCleaned
                  ? formatDate(selectedDrive.lastCleaned)
                  : '—',
            },
            { label: 'Cleaning Status', value: selectedNeedsCleaning ? 'Required' : driveStatusQuery.data?.cleaning ?? 'Good' },
            { label: 'Drive Health', value: driveStatusQuery.data?.overall ?? '—' },
            { label: 'Last Loaded', value: driveStatisticsQuery.data?.lastLoaded ? formatDate(driveStatisticsQuery.data.lastLoaded) : '—' },
            {
              label: 'Cleaning Media',
              value: selectedCleaningReport ? `${selectedCleaningReport.mediaBarcode} · uses ${selectedCleaningReport.useCount}` : '—',
            },
          ]}
        />
      ) : null}

      {operationMessage ? (
        <div className="rounded-md border border-emerald-700 bg-emerald-900/20 px-4 py-3 text-sm text-emerald-200">{operationMessage}</div>
      ) : null}

      {operationError ? <ErrorMessage error={operationError} /> : null}

      <OperationsPanel
        title="Drive Operations"
        subtitle="Vary-on, vary-off, force clean, and refresh are wired to the AML drive endpoints with optimistic updates."
        actions={[
          {
            label: isVaryingOn ? 'Vary-On…' : 'Vary-On',
            onClick: () => void handleVaryOn(),
            disabled: !selectedDrive || !['OFFLINE', 'FAILED'].includes(selectedDriveState) || isVaryingOn || isVaryingOff || isCleaning,
            variant: 'primary',
          },
          {
            label: isVaryingOff ? 'Vary-Off…' : 'Vary-Off',
            onClick: () => void handleVaryOff(),
            disabled: !selectedDrive || !['IDLE', 'READY'].includes(selectedDriveState) || isVaryingOn || isVaryingOff || isCleaning,
            variant: 'secondary',
          },
          {
            label: isCleaning ? 'Force Clean…' : 'Force Clean',
            onClick: () => void handleForceClean(),
            disabled: !selectedDrive || isVaryingOn || isVaryingOff || isCleaning,
            variant: 'danger',
          },
          {
            label: isRefreshing ? 'Refresh…' : 'Refresh',
            onClick: () => void handleRefresh(),
            disabled: isRefreshing || isVaryingOn || isVaryingOff || isCleaning,
            variant: 'secondary',
          },
        ]}
      />
    </div>
  );
}
