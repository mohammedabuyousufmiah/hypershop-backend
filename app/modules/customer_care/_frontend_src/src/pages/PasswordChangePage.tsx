import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export default function PasswordChangePage() {
  const { changePassword, logout } = useAuth();
  const navigate = useNavigate();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next.length < 12) return setError("New password must be at least 12 characters");
    if (next !== confirm) return setError("Passwords don't match");
    if (next === current) return setError("New password must differ from current");

    setSubmitting(true);
    try {
      await changePassword(current, next);
      navigate("/inbox", { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(typeof err.detail === "string" ? err.detail : "Change failed");
      } else {
        setError("Network error");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-full flex items-center justify-center bg-slate-100 p-4">
      <div className="card w-full max-w-md p-8">
        <div className="badge-warn mb-4">First-time login — password change required</div>
        <h1 className="text-xl font-semibold mb-1">Set a new password</h1>
        <p className="text-sm text-slate-500 mb-6">
          Use at least 12 characters, mixing letters, numbers, and symbols.
        </p>

        <form className="space-y-4" onSubmit={onSubmit}>
          <input
            type="password"
            autoComplete="current-password"
            required
            placeholder="Current password"
            className="input"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            placeholder="New password (min 12 chars)"
            className="input"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <input
            type="password"
            autoComplete="new-password"
            required
            placeholder="Confirm new password"
            className="input"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {error && (
            <div className="badge-error w-full justify-center py-2 text-center">{error}</div>
          )}
          <button type="submit" disabled={submitting} className="btn-primary w-full py-2.5">
            {submitting ? "Updating…" : "Update password"}
          </button>
        </form>

        <button onClick={() => void logout()} className="btn-ghost w-full mt-3">
          Cancel and sign out
        </button>
      </div>
    </div>
  );
}
