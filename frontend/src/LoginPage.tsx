import { useState, type FormEvent } from "react";
import { login } from "./api";

interface Props {
  /** Told when the login worked: the session is in the browser's cookies by then. */
  onLoggedIn: () => void;
}

/**
 * The page where a user logs in: a username, a password, and a button. Everything of
 * the app but the main page needs it. A wrong username or password is one message
 * (the backend does not say which was wrong).
 */
export default function LoginPage({ onLoggedIn }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !username || !password) return;
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      onLoggedIn();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Request failed");
      setBusy(false);
    }
  }

  return (
    <main className="main-page login-page review-styled">
      <form className="login-box" aria-label="Log in" onSubmit={submit}>
        <h1>Log in</h1>
        <label className="login-field">
          Username
          <input
            className="styled-input"
            value={username}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={200}
            autoFocus
            disabled={busy}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className="login-field">
          Password
          <input
            className="styled-input"
            type="password"
            value={password}
            autoComplete="current-password"
            maxLength={1000}
            disabled={busy}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="login-error">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || !username || !password}>
          Log in
        </button>
      </form>
    </main>
  );
}
