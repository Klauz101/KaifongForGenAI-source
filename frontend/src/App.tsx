import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Loading } from "./components/States";
import Alerts from "./pages/Alerts";
import Audit from "./pages/Audit";
import Privacy from "./pages/Privacy";
import CallDetail from "./pages/CallDetail";
import ElderDetail from "./pages/ElderDetail";
import DemoControl from "./pages/DemoControl";
import DigestPage from "./pages/DigestPage";
import Elders from "./pages/Elders";
import FamilyHome from "./pages/FamilyHome";
import Login from "./pages/Login";
import Metrics from "./pages/Metrics";
import PhoneSimulator from "./pages/PhoneSimulator";
import Today from "./pages/Today";

export default function App() {
  const { user, loading } = useAuth();
  const { pathname } = useLocation();
  if (pathname === "/privacy") return <Privacy />; // readable without signing in
  if (loading) return <Loading />;
  if (!user) return <Login />;

  if (user.role === "family") {
    return (
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<FamilyHome />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    );
  }
  return (
    <Routes>
      <Route path="simulator/:elderId" element={<PhoneSimulator />} />
      <Route element={<Layout />}>
        <Route index element={<Today />} />
        {user.role === "admin" && <Route path="metrics" element={<Metrics />} />}
        {user.role === "admin" && <Route path="audit" element={<Audit />} />}
        {user.role === "admin" && <Route path="demo" element={<DemoControl />} />}
        <Route path="elders" element={<Elders />} />
        <Route path="elders/:id" element={<ElderDetail />} />
        <Route path="elders/:id/digest" element={<DigestPage />} />
        <Route path="calls/:id" element={<CallDetail />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
