import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import Layout from './components/layout/Layout';
import { isAuthenticated } from './lib/auth';
import Dashboard from './pages/Dashboard';
import DriveOperations from './pages/DriveOperations';
import Drives from './pages/Drives';
import Health from './pages/Health';
import ImportExport from './pages/ImportExport';
import InventoryScan from './pages/InventoryScan';
import Jobs from './pages/Jobs';
import Libraries from './pages/Libraries';
import Library from './pages/Library';
import LibraryIE from './pages/LibraryIE';
import Login from './pages/Login';
import LtfsBrowse from './pages/LtfsBrowse';
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
import SystemNetwork from './pages/SystemNetwork';

function ProtectedLayout() {
  const location = useLocation();

  if (!isAuthenticated()) {
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
        <Route path="dashboard" element={<Navigate to="/" replace />} />
        <Route path="libraries" element={<Libraries />} />
        <Route path="health" element={<Health />} />
        <Route path="library" element={<Dashboard />} />
        <Route path="library/inventory" element={<Library />} />
        <Route path="library/ie" element={<LibraryIE />} />
        <Route path="partitions" element={<Partitions />} />
        <Route path="media" element={<Media />} />
        <Route path="media/pools" element={<MediaPools />} />
        <Route path="media/ltfs" element={<LtfsBrowse />} />
        <Route path="drives" element={<Drives />} />
        <Route path="drives/ops" element={<DriveOperations />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="operations/move" element={<MoveOperations />} />
        <Route path="operations/inventory" element={<InventoryScan />} />
        <Route path="operations/ie" element={<ImportExport />} />
        <Route path="system" element={<System />} />
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
      <RoutedApp />
    </BrowserRouter>
  );
}
