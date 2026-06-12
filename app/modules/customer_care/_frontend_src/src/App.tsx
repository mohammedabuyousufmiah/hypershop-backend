import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import InboxPage from "./pages/InboxPage";
import LoginPage from "./pages/LoginPage";
import PasswordChangePage from "./pages/PasswordChangePage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading, mustChange } = useAuth();
  if (loading) {
    return (
      <div className="min-h-full grid place-items-center text-slate-500 text-sm">Loading…</div>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (mustChange) return <Navigate to="/change-password" replace />;
  return <>{children}</>;
}

function PasswordChangeGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/change-password"
          element={
            <PasswordChangeGate>
              <PasswordChangePage />
            </PasswordChangeGate>
          }
        />
        <Route
          path="/inbox"
          element={
            <ProtectedRoute>
              <InboxPage />
            </ProtectedRoute>
          }
        />
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Routes>
    </AuthProvider>
  );
}
