import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, token } from "../api/client";
import type { Learner } from "../api/types";

interface Auth {
  learner: Learner | null;
  /** Distinguishes "not signed in" from "we have not asked yet", which otherwise flashes the
   *  login screen at a signed-in learner on every reload. */
  checking: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name: string) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [learner, setLearner] = useState<Learner | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!token.get()) {
      setChecking(false);
      return;
    }
    // A token in storage may be expired or signed with a key the server no longer has, so it
    // is checked rather than trusted.
    api
      .me()
      .then(setLearner)
      .catch(() => token.clear())
      .finally(() => setChecking(false));
  }, []);

  const value = useMemo<Auth>(
    () => ({
      learner,
      checking,
      signIn: async (email, password) => {
        const session = await api.login(email, password);
        token.set(session.token);
        setLearner({ user_id: session.user_id, email: session.email, name: session.name });
      },
      signUp: async (email, password, name) => {
        const session = await api.signup(email, password, name);
        token.set(session.token);
        setLearner({ user_id: session.user_id, email: session.email, name: session.name });
      },
      signOut: () => {
        token.clear();
        setLearner(null);
      },
    }),
    [learner, checking],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const found = useContext(AuthContext);
  if (!found) throw new Error("useAuth must be used inside AuthProvider");
  return found;
}
