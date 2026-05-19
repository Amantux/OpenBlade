import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/layout/Layout';
import Archive from './pages/Archive';
import Catalog from './pages/Catalog';
import Dashboard from './pages/Dashboard';
import Health from './pages/Health';
import Jobs from './pages/Jobs';
import Library from './pages/Library';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="library" element={<Library />} />
          <Route path="jobs" element={<Jobs />} />
          <Route path="archive" element={<Archive />} />
          <Route path="catalog" element={<Catalog />} />
          <Route path="health" element={<Health />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
