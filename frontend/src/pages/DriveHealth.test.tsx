import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import DriveHealth from './DriveHealth';
import { ApiError } from '../api/client';
import type { DriveHealthListing } from '../api/driveHealth';

const driveHealthModule = vi.hoisted(() => ({
  getDriveHealth: vi.fn<(device?: string) => Promise<DriveHealthListing>>(),
}));

vi.mock('../api/driveHealth', () => driveHealthModule);

const LISTING: DriveHealthListing = {
  drives: [
    {
      device: '/dev/sg1',
      inquiry: {
        deviceType: 'tape',
        vendor: 'IBM',
        product: 'ULT3580-TD8',
        revision: 'J4C1',
        serial: '1068000073',
      },
      tapeAlertSupported: true,
      tapeAlertReason: null,
      worstSeverity: 'critical',
      flagsRead: 64,
      activeFlags: [
        { number: 4, name: 'Media', severity: 'critical' },
        { number: 3, name: 'Hard Error', severity: 'warning' },
        { number: 56, name: 'Loading Failure', severity: 'unknown' },
      ],
    },
    {
      device: '/dev/sg2',
      inquiry: {
        deviceType: 'tape',
        vendor: 'IBM',
        product: 'ULT3580-TD8',
        revision: 'J4C1',
        serial: '',
      },
      tapeAlertSupported: false,
      tapeAlertReason: 'no TapeAlert page in sg_logs output',
      worstSeverity: null,
      flagsRead: 0,
      activeFlags: [],
    },
  ],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DriveHealth />
    </QueryClientProvider>,
  );
}

describe('DriveHealth', () => {
  beforeEach(() => {
    driveHealthModule.getDriveHealth.mockReset();
    driveHealthModule.getDriveHealth.mockResolvedValue(LISTING);
  });

  it('renders inquiry data and active TapeAlert flags per drive', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('/dev/sg1')).toBeTruthy();
    });
    expect(screen.getAllByText(/ULT3580-TD8 · rev J4C1/).length).toBe(2);
    expect(screen.getByText(/Serial 1068000073/)).toBeTruthy();
    expect(screen.getByText('Media')).toBeTruthy();
    expect(screen.getByText('Hard Error')).toBeTruthy();
    expect(screen.getByText('TapeAlert critical')).toBeTruthy();
    // Severity badges, including the unclassified one — never rendered as fine.
    expect(screen.getByText('critical')).toBeTruthy();
    expect(screen.getByText('warning')).toBeTruthy();
    expect(screen.getByText('unknown')).toBeTruthy();
  });

  it('reports a drive without the TapeAlert log page as a fact, not a failure', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('/dev/sg2')).toBeTruthy();
    });
    expect(screen.getByText(/does not implement the TapeAlert log page/)).toBeTruthy();
    expect(screen.getByText(/no TapeAlert page in sg_logs output/)).toBeTruthy();
    expect(screen.getByText('Serial (none reported) · type tape')).toBeTruthy();
  });

  it('inspects one named device when asked', async () => {
    renderPage();
    await waitFor(() => {
      expect(driveHealthModule.getDriveHealth).toHaveBeenCalledWith(undefined);
    });

    fireEvent.change(screen.getByLabelText('Device (optional)'), {
      target: { value: '/dev/nst0' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Inspect' }));

    await waitFor(() => {
      expect(driveHealthModule.getDriveHealth).toHaveBeenCalledWith('/dev/nst0');
    });
  });

  it('renders the 503 as setup instructions rather than an error', async () => {
    driveHealthModule.getDriveHealth.mockRejectedValue(
      new ApiError(
        'Real hardware operations require OPENBLADE_BACKEND=real and '
          + 'OPENBLADE_REAL_HARDWARE_ENABLED=true',
        503,
        'impact',
        'action',
      ),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('Real hardware is not enabled')).toBeTruthy();
    });
    expect(screen.getAllByText(/OPENBLADE_REAL_HARDWARE_ENABLED=true/).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull();
  });

  it('renders any other failure as a retryable error', async () => {
    driveHealthModule.getDriveHealth.mockRejectedValue(
      new ApiError('sg_logs is not installed', 500, 'impact', 'action'),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy();
    });
    expect(screen.getAllByText(/sg_logs is not installed/).length).toBeGreaterThan(0);
  });

  it('says so when no drive was discovered', async () => {
    driveHealthModule.getDriveHealth.mockResolvedValue({ drives: [] });
    renderPage();

    await waitFor(() => {
      expect(screen.getByText('No tape drives discovered')).toBeTruthy();
    });
  });
});
