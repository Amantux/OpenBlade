import { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getDrive, getDriveStatistics, getDriveStatus, listDriveCleaningReports, listDrives, listDrivesNeedingCleaning, type Drive } from '../api/drives';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (['FAULTED', 'FAILED', 'OFFLINE'].includes(state)) return 'red';
  if (['BUSY', 'MOUNTED', 'CLEANING'].includes(state)) return 'amber';
  if (['IDLE', 'READY', 'EMPTY'].includes(state)) return 'green';
  return 'blue';
}

function getDriveId(serialNumber: string): string {
  const match = serialNumber.match(/(\d+)$/);
  return match ? `D${match[1].padStart(3, '0')}` : serialNumber;
}

function getOperationalState(drive?: Drive): string {
  if (!drive) return 'UNKNOWN';
  if (String(drive.status).toUpperCase() === 'OFFLINE') return 'OFFLINE';
  return String(drive.state).toUpperCase();
}

export default function Drives() {
  const queryClient = useQueryClient();
  const [selectedSerialNumber, setSelectedSerialNumber] = useState<string>();

  const drivesQuery = useQuery({ queryKey: ['drives'], queryFn: listDrives, refetchInterval: 30_000 });
  const cleaningNeedsQuery = useQuery({ queryKey: ['drive-cleaning-needs'], queryFn: listDrivesNeedingCleaning, refetchInterval: 30_000 });
  const cleaningReportsQuery = useQuery({ queryKey: ['drive-cleaning-report'], queryFn: listDriveCleaningReports, refetchInterval: 30_000 });

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
  const selectedCleaningReport = cleaningReportsQuery.data?.find((report) => report.serialNumber === activeSerialNumber);
  const selectedNeedsCleaning = cleaningNeedsQuery.data?.some((drive) => drive.serialNumber === activeSerialNumber) ?? false;
  const selectedDriveState = getOperationalState(selectedDrive);
  const queryError = drivesQuery.error ?? cleaningNeedsQuery.error ?? cleaningReportsQuery.error ?? driveDetailQuery.error ?? driveStatusQuery.error ?? driveStatisticsQuery.error;

  const summary = useMemo(() => ({
    online: drives.filter((drive) => String(drive.status).toUpperCase() !== 'OFFLINE').length,
    loaded: drives.filter((drive) => drive.loadedMedia?.barcode).length,
    cleaning: cleaningNeedsQuery.data?.length ?? 0,
  }), [cleaningNeedsQuery.data, drives]);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['drives'] }),
      queryClient.invalidateQueries({ queryKey: ['drive-cleaning-needs'] }),
      queryClient.invalidateQueries({ queryKey: ['drive-cleaning-report'] }),
      activeSerialNumber ? queryClient.invalidateQueries({ queryKey: ['drive', activeSerialNumber] }) : Promise.resolve(),
      activeSerialNumber ? queryClient.invalidateQueries({ queryKey: ['drive-status', activeSerialNumber] }) : Promise.resolve(),
      activeSerialNumber ? queryClient.invalidateQueries({ queryKey: ['drive-statistics', activeSerialNumber] }) : Promise.resolve(),
    ]);
  }

  if (drivesQuery.isLoading || cleaningNeedsQuery.isLoading || cleaningReportsQuery.isLoading) {
    return <Spinner />;
  }
  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => void refresh()} />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Drives</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Drive overview</h1>
            <p className="mt-2 text-sm text-slate-400">
              Read-only fleet status for serial numbers, firmware, loaded media, and health. Operational actions now live on /drives/ops.
            </p>
          </div>
          <Button variant="secondary" onClick={() => void refresh()}>Refresh</Button>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3 xl:grid-cols-4">
          {[
            ['Total Drives', drives.length],
            ['Online', summary.online],
            ['Loaded', summary.loaded],
            ['Needs Cleaning', summary.cleaning],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">{label}</div>
              <div className="mt-2 text-lg font-semibold text-slate-100">{value}</div>
            </div>
          ))}
        </div>
      </Card>

      <NorthPanel
        title="Installed Drives"
        subtitle="Serial, state, loaded tape, firmware, and health are shown without operational controls."
        columns={[
          { key: 'driveId', header: 'Drive ID', render: (row: Drive) => getDriveId(row.serialNumber) },
          { key: 'serialNumber', header: 'Serial', render: (row: Drive) => row.serialNumber },
          {
            key: 'state',
            header: 'State',
            render: (row: Drive) => <Badge variant={stateVariant(getOperationalState(row))}>{getOperationalState(row)}</Badge>,
          },
          { key: 'loaded', header: 'Loaded Tape', render: (row: Drive) => row.loadedMedia?.barcode ?? 'Empty' },
          { key: 'firmware', header: 'Firmware', render: (row: Drive) => row.firmware || '—' },
          {
            key: 'health',
            header: 'Health',
            render: (row: Drive) => row.serialNumber === activeSerialNumber ? (driveStatusQuery.data?.overall ?? '—') : 'Select row',
          },
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
          subtitle="Detailed read-only state for the selected drive."
          items={[
            { label: 'State', value: <Badge variant={stateVariant(selectedDriveState)}>{selectedDriveState}</Badge> },
            { label: 'Firmware', value: selectedDrive.firmware || '—' },
            { label: 'Loaded Tape', value: selectedDrive.loadedMedia?.barcode ?? 'Empty' },
            { label: 'Location', value: selectedDrive.location || '—' },
            { label: 'Health', value: driveStatusQuery.data?.overall ?? '—' },
            { label: 'Cleaning', value: selectedNeedsCleaning ? 'Required' : driveStatusQuery.data?.cleaning ?? 'Good' },
            { label: 'Load Count', value: driveStatisticsQuery.data?.loadCount ?? selectedDrive.loadCount },
            { label: 'Error Count', value: driveStatisticsQuery.data?.errorCount ?? selectedDrive.errorCount },
            { label: 'Last Loaded', value: driveStatisticsQuery.data?.lastLoaded ? formatDate(driveStatisticsQuery.data.lastLoaded) : '—' },
            { label: 'Last Cleaned', value: selectedCleaningReport?.lastCleaned ? formatDate(selectedCleaningReport.lastCleaned) : selectedDrive.lastCleaned ? formatDate(selectedDrive.lastCleaned) : '—' },
            { label: 'Cleaning Media', value: selectedCleaningReport?.mediaBarcode ?? '—' },
            { label: 'Interface', value: selectedDrive.interface ?? 'FC' },
          ]}
        />
      ) : null}
    </div>
  );
}
