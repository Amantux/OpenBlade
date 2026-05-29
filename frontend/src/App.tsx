import { useEffect } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation, useParams } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Spinner from './components/ui/Spinner';
import { getActiveLibraryId, setActiveLibraryId } from './lib/activeLibrary';
import { AuthProvider, useAuth } from './lib/auth-context';
import AdminSafetyPage from './pages/AdminSafetyPage';
import AdminSecurityPage from './pages/AdminSecurityPage';
import Archive from './pages/Archive';
import Catalog from './pages/Catalog';
import CatalogRebuildPage from './pages/CatalogRebuildPage';
import CatalogStatusPage from './pages/CatalogStatusPage';
import Dashboard from './pages/Dashboard';
import Drives from './pages/Drives';
import ErrorCodesPage from './pages/ErrorCodesPage';
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
import TestRunner from './pages/TestRunner';

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

function ActiveLibraryPathRedirect({ suffix }: { suffix: string }) {
  const activeLibraryId = getActiveLibraryId();
  if (!activeLibraryId) {
    return <Navigate to="/libraries" replace />;
  }
  return <Navigate to={`/libraries/${activeLibraryId}${suffix}`} replace />;
}

function LibraryScopedOutlet() {
  const { libraryId } = useParams();

  useEffect(() => {
    if (libraryId) {
      setActiveLibraryId(libraryId, `Library ${libraryId}`);
    }
  }, [libraryId]);

  return <Outlet />;
}

function RoutedApp() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="dashboard" element={<Dashboard />} />

        <Route path="libraries" element={<Libraries />} />
        <Route path="libraries/:libraryId" element={<LibraryScopedOutlet />}>
          <Route index element={<Navigate to="items/overview" replace />} />
          <Route path="items" element={<Navigate to="overview" replace />} />
          <Route path="items/overview" element={<LibraryMap />} />
          <Route path="items/map" element={<LibraryMap />} />
          <Route path="items/inventory" element={<Library />} />
          <Route path="items/cartridges" element={<Media />} />
          <Route path="items/drives" element={<Drives />} />
          <Route path="items/partitions" element={<Partitions />} />
          <Route path="items/ie" element={<LibraryIE />} />
          <Route path="items/ltfs" element={<LtfsBrowse />} />
          <Route path="items/jobs" element={<Jobs />} />
          <Route path="items/move" element={<MoveOperations />} />
          <Route path="items/inventory-scan" element={<InventoryScan />} />
          <Route path="items/import-export" element={<ImportExport />} />
          <Route path="admin/status" element={<LibraryStatusPage />} />
          <Route path="admin/diagnostics" element={<SystemDiagnostics />} />
          <Route path="admin/safety" element={<AdminSafetyPage />} />
        </Route>

        <Route path="health" element={<Navigate to="/system/health" replace />} />
        <Route path="library" element={<ActiveLibraryPathRedirect suffix="/items/map" />} />
        <Route path="library/inventory" element={<ActiveLibraryPathRedirect suffix="/items/inventory" />} />
        <Route path="library/ie" element={<ActiveLibraryPathRedirect suffix="/items/ie" />} />
        <Route path="partitions" element={<ActiveLibraryPathRedirect suffix="/items/partitions" />} />
        <Route path="media" element={<ActiveLibraryPathRedirect suffix="/items/cartridges" />} />
        <Route path="media/ltfs" element={<ActiveLibraryPathRedirect suffix="/items/ltfs" />} />
        <Route path="drives" element={<ActiveLibraryPathRedirect suffix="/items/drives" />} />
        <Route path="drives/ops" element={<ActiveLibraryPathRedirect suffix="/items/drives" />} />
        <Route path="jobs" element={<ActiveLibraryPathRedirect suffix="/items/jobs" />} />
        <Route path="operations/move" element={<ActiveLibraryPathRedirect suffix="/items/move" />} />
        <Route path="operations/inventory" element={<ActiveLibraryPathRedirect suffix="/items/inventory-scan" />} />
        <Route path="operations/ie" element={<ActiveLibraryPathRedirect suffix="/items/import-export" />} />
        <Route path="media/pools" element={<MediaPools />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="catalog/rebuild" element={<CatalogRebuildPage />} />
        <Route path="catalog/manifests" element={<ManifestVersionsPage />} />
        <Route path="archive" element={<Archive />} />
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
        <Route path="system/test-runner" element={<TestRunner />} />
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
