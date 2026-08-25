import { initializeApp } from 'firebase/app';
import { getAuth, connectAuthEmulator } from 'firebase/auth';

/**
 * Firebase client config. These values are NOT secrets - they identify
 * your project to Firebase and are visible in any web app's bundle.
 * What actually protects your data is Firebase Auth plus the backend's
 * own role and tenant checks, not hiding these.
 *
 * Find them in Firebase Console -> Project Settings -> General ->
 * "Your apps" -> Web app -> SDK setup and configuration.
 */
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);

// Local development runs against the Auth emulator, which doesn't check the
// API key - so the values above can be dummies while VITE_USE_AUTH_EMULATOR
// is on. In production the flag is absent and the app talks to real Firebase.
//
// VITE_FIREBASE_PROJECT_ID has to match the backend's project either way:
// the backend verifies each token's "aud" claim against its own project, so
// a mismatch fails at login with a message that doesn't mention config.
if (import.meta.env.VITE_USE_AUTH_EMULATOR === 'true') {
  connectAuthEmulator(auth, 'http://127.0.0.1:9099', { disableWarnings: true });
}
