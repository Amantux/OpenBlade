import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Spinner from './components/ui/Spinner';
import { AuthProvider, useAuth } from './lib/auth-context';
import AdminSafetyPage from './pages/AdminSafetyPage';
import AdminSecurityPage from './pages/AdminSecurityPage';
import Archive from './pages/Archive';
import Catalog from './pages/Catalog';
import CatalogRebuildPage from './pages/CatalogRebuildPage';
import CatalogStatusPage from './pages/CatalogStatusPage';
import Dashboard from './pages/Dashboard';
import DriveOperations from './pages/DriveOperations';
import Drives from './pages/Drives';
import ErrorCodesPage from './pages/ErrorCodesPage';
import FileStation from './pages/FileStation';
import GatewayPage from './pages/Gateway';
import Health from './pages/Health';
import ImportExport from './pages/ImportExport';
import InventoryScan from './pages/InventoryScan';
import Jobs from './pages/Jobs';
import Libraries from './pages/Libraries';
import Library from './pages/Library';
import LibraryIE from './pages/LibraryIE';
import LibraryMap from './pages/LibraryMap';
import LibraryStatusPage from './pages/LibraryStatusPage';
import Login from './pages/Login';
import LtfsBrowse from './pages/LtfsBrowse';
import ManifestVersionsPage from './pages/ManifestVersionsPage';
import Media from './pages/Media';
import MediaPools from './pages/MediaPools';
import Mounts from './pages/Mounts';
import MoveOperations from './pages/MoveOperations';
import Partitions from './pages/Partitions';
import ReportsActivity from './pages/ReportsActivity';
import ReportsEvents from './pages/ReportsEvents';
import ReportsRas from './pages/ReportsRas';
import System from './pages/System';
import SystemConfiguration from './pages/SystemConfiguration';
import SystemDiagnostics from './pages/SystemDiagnostics';
import SystemFirmware from './pages/SystemFirmware';
import SystemHealthPage from './pages/SystemHealthPage';
import SystemNetwork from './pages/SystemNetwork';
import VirtualFileBrowserPage from './pages/VirtualFileBrowserPage';
import ArchivePlanning from './pages/nas/ArchivePlanning';
import CacheDrives from './pages/nas/CacheDrives';
import DatasetDetails from './pages/nas/DatasetDetails';
import RestoreQueue from './pages/nas/RestoreQueue';
import SourceStreaming from './pages/nas/SourceStreaming';
import StoragePolicies from './pages/nas/StoragePolicies';
import VirtualPools from './pages/nas/VirtualPools';

function ProtectedLayout() {
  const location = useLocation();
  const auth = useAuth();

  if (auth.isChecking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-quantum-panel">
        <Spinner />
      </div>
    );
  }

  if (!auth.isAuthenticated) {
    return <Navigate to={`/login?redirect=${encodeURIComponent(`${location.pathname}${location.search}${location.hash}` || '/')}`} replace />;
  }

  return <Layout />;
}

function RoutedApp() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="libraries" element={<Libraries />} />
        <Route path="health" element={<Health />} />
        <Route path="library" element={<LibraryMap />} />
        <Route path="library/inventory" element={<Library />} />
        <Route path="library/ie" element={<LibraryIE />} />
        <Route path="partitions" element={<Partitions />} />
        <Route path="media" element={<Media />} />
        <Route path="media/pools" element={<MediaPools />} />
        <Route path="media/ltfs" element={<LtfsBrowse />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="catalog/rebuild" element={<CatalogRebuildPage />} />
        <Route path="catalog/manifests" element={<ManifestVersionsPage />} />
        <Route path="archive" element={<Archive />} />
        <Route path="drives" element={<Drives />} />
        <Route path="drives/ops" element={<DriveOperations />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="operations/move" element={<MoveOperations />} />
        <Route path="operations/inventory" element={<InventoryScan />} />
        <Route path="operations/ie" element={<ImportExport />} />
        <Route path="storage/policies" element={<StoragePolicies />} />
        <Route path="storage/cache-drives" element={<CacheDrives />} />
        <Route path="storage/source-streaming" element={<SourceStreaming />} />
        <Route path="storage/archive-planning" element={<ArchivePlanning />} />
        <Route path="storage/virtual-pools" element={<VirtualPools />} />
        <Route path="storage/restore-queue" element={<RestoreQueue />} />
        <Route path="storage/dataset-details" element={<DatasetDetails />} />
        <Route path="storage/shares" element={<Mounts />} />
        <Route path="file-station" element={<FileStation />} />
        <Route path="gateway" element={<GatewayPage />} />
        <Route path="files/browse" element={<VirtualFileBrowserPage />} />
        <Route path="admin/security" element={<AdminSecurityPage />} />
        <Route path="admin/safety" element={<AdminSafetyPage />} />
        <Route path="system" element={<System />} />
        <Route path="system/info" element={<System />} />
        <Route path="system/health" element={<SystemHealthPage />} />
        <Route path="system/error-codes" element={<ErrorCodesPage />} />
        <Route path="system/library" element={<LibraryStatusPage />} />
        <Route path="system/catalog" element={<CatalogStatusPage />} />
        <Route path="system/network" element={<SystemNetwork />} />
        <Route path="system/config" element={<SystemConfiguration />} />
        <Route path="system/firmware" element={<SystemFirmware />} />
        <Route path="system/diagnostics" element={<SystemDiagnostics />} />
        <Route path="reports/ras" element={<ReportsRas />} />
        <Route path="reports/events" element={<ReportsEvents />} />
        <Route path="reports/activity" element={<ReportsActivity />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <RoutedApp />
      </AuthProvider>
    </BrowserRouter>
  );
}
