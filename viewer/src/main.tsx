import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router';
import './styles/tokens.css';
import './styles/app.css';
import './index.css';
import './styles/room.css'; // the 3D room as the landing's hall: after index.css, which it restyles
import Shell from './app/Shell';
import Dashboard from './screens/Dashboard';
import Pipeline from './screens/Pipeline';
import Studies from './screens/Studies';
import Proofs from './screens/Proofs';
import Hologram from './screens/Hologram';
import NotFound from './screens/NotFound';
import Login from './screens/Login';

// Vesalius, one product: the dashboard screens inside the Shell, the hologram screens full-bleed.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter basename="/app">
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Dashboard />} />
          <Route path="pipeline" element={<Pipeline />} />
          <Route path="studies" element={<Studies />} />
          <Route path="proofs" element={<Proofs />} />
          <Route path="login" element={<Login />} />
          <Route path="*" element={<NotFound />} />
        </Route>
        <Route path="hologram/:studyId" element={<Hologram />} />
        <Route path="body" element={<Hologram scene="atlas" />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
