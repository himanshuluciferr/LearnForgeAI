import { useState, type FormEvent } from "react";
import { useAuth } from "./AuthContext";
import library from "../assets/library.jpg";

const MIN_PASSWORD = 8;

export function SignIn() {
  const { signIn, signUp } = useAuth();
  const [joining, setJoining] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await (joining ? signUp(email, password, name) : signIn(email, password));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate">
      {/* Decorative: the promise is already made in words below, so a screen reader gains
          nothing from the picture and an alt text here would just be noise. */}
      <img className="gate-photo" src={library} alt="" aria-hidden="true" />

      <header className="gate-brand">
        <p className="gate-mark">Mentora AI</p>
        <p className="gate-line">Tell it what you want to learn. It writes you the book.</p>
      </header>

      <div className="gate-panel">
        <form className="auth" onSubmit={submit}>
          <h1 className="gate-title">{joining ? "Create your account" : "Sign in"}</h1>

          {joining && (
            <label>
              Name
              <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </label>
          )}
          <label>
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
            />
          </label>
          <label>
            Password
            <input
              type="password"
              required
              minLength={MIN_PASSWORD}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={joining ? "new-password" : "current-password"}
            />
          </label>
          {joining && <p className="hint">At least {MIN_PASSWORD} characters.</p>}

          {error && <p className="error">{error}</p>}

          <button type="submit" disabled={busy}>
            {busy ? "One moment…" : joining ? "Create account" : "Sign in"}
          </button>
          <button
            type="button"
            className="link"
            onClick={() => {
              setJoining(!joining);
              setError(null);
            }}
          >
            {joining ? "I already have an account" : "I need an account"}
          </button>
        </form>
      </div>
    </div>
  );
}
