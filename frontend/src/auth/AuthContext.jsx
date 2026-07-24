import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import {
  onAuthStateChanged,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut as fbSignOut,
} from 'firebase/auth';
import { auth } from '../firebase';
import { api } from '../api/client';

const AuthContext = createContext(null);

/**
 * Two separate questions, deliberately kept apart:
 *
 *   1. Who are you?          -> Firebase. Answered by having an account.
 *   2. Which business, and   -> our backend. Answered only after signing
 *      what may you do?         up for a business or accepting an invite.
 *
 * Having an answer to (1) but not (2) is a normal state, not an error - it's
 * exactly where a brand-new user stands, and it's what sends them to the
 * signup screen. The backend marks it with a 409 so it can't be confused
 * with a genuine permission problem.
 */
export function AuthProvider({ children }) {
  const [firebaseUser, setFirebaseUser] = useState(null);
  const [profile, setProfile] = useState(null);
  const [needsSignup, setNeedsSignup] = useState(false);
  const [loading, setLoading] = useState(true);
  const [profileError, setProfileError] = useState('');

  const loadProfile = useCallback(async () => {
    setProfileError('');
    try {
      setProfile(await api.getMe());
      setNeedsSignup(false);
    } catch (e) {
      setProfile(null);
      if (e.status === 409) {
        setNeedsSignup(true);      // signed in, hasn't joined a business yet
      } else {
        setNeedsSignup(false);
        setProfileError(e.message);
      }
    }
  }, []);

  useEffect(() => {
    return onAuthStateChanged(auth, async (u) => {
      setFirebaseUser(u);
      if (!u) {
        setProfile(null);
        setNeedsSignup(false);
        setProfileError('');
        setLoading(false);
        return;
      }
      await loadProfile();
      setLoading(false);
    });
  }, [loadProfile]);

  async function signIn(email, password) {
    await signInWithEmailAndPassword(auth, email, password);
  }

  async function signUp(email, password) {
    await createUserWithEmailAndPassword(auth, email, password);
  }

  async function signOut() {
    await fbSignOut(auth);
    setProfile(null);
    setNeedsSignup(false);
  }

  const can = (capability) => !!profile?.capabilities?.includes(capability);

  return (
    <AuthContext.Provider value={{
      firebaseUser, profile, needsSignup, loading, profileError,
      signIn, signUp, signOut, can, reloadProfile: loadProfile,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
