import {
  getApps,
  initializeApp,
  type FirebaseApp,
  type FirebaseOptions,
} from "firebase/app";
import {
  browserLocalPersistence,
  createUserWithEmailAndPassword,
  getAuth,
  GoogleAuthProvider,
  OAuthProvider,
  onAuthStateChanged,
  setPersistence,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  type Auth,
  type User,
} from "firebase/auth";
import type { FirebaseServerConfig } from "./serverConfig";
import { firebaseOptions } from "./firebaseConfig";

let activeAuth: Auth | null = null;

const googleProvider = new GoogleAuthProvider();
googleProvider.setCustomParameters({ prompt: "select_account" });

const appleProvider = new OAuthProvider("apple.com");
appleProvider.addScope("email");
appleProvider.addScope("name");

export async function configureFirebase(
  config: FirebaseServerConfig,
): Promise<void> {
  const options = firebaseOptions(config);
  const app = firebaseApp(options);
  const auth = getAuth(app);
  await setPersistence(auth, browserLocalPersistence);
  activeAuth = auth;
}

export function subscribeToFirebaseUser(
  onUserChanged: (user: User | null) => void,
): () => void {
  return onAuthStateChanged(requireAuth(), onUserChanged);
}

export async function signInWithEmail(email: string, password: string): Promise<void> {
  await signInWithEmailAndPassword(requireAuth(), email, password);
}

export async function createEmailAccount(
  email: string,
  password: string,
): Promise<void> {
  await createUserWithEmailAndPassword(requireAuth(), email, password);
}

export async function signInWithGoogle(): Promise<void> {
  await signInWithPopup(requireAuth(), googleProvider);
}

export async function signInWithApple(): Promise<void> {
  await signInWithPopup(requireAuth(), appleProvider);
}

export async function signOutOfFirebase(): Promise<void> {
  await signOut(requireAuth());
}

export async function getFirebaseIdToken(forceRefresh = false): Promise<string> {
  const user = requireAuth().currentUser;
  if (user === null) {
    throw new Error("Sign in before starting a voice session.");
  }

  return user.getIdToken(forceRefresh);
}

function firebaseApp(options: FirebaseOptions): FirebaseApp {
  const name = `voxos_${safeName(options.projectId ?? "server")}_${hashOptions(options)}`;
  const existing = getApps().find((candidate) => candidate.name === name);
  return existing ?? initializeApp(options, name);
}

function safeName(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 48);
}

function hashOptions(options: FirebaseOptions): string {
  const source = `${options.apiKey ?? ""}|${options.appId ?? ""}|${options.authDomain ?? ""}|${options.storageBucket ?? ""}`;
  let hash = 2166136261;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function requireAuth(): Auth {
  if (activeAuth === null) {
    throw new Error("Choose a VoxOS server before signing in.");
  }
  return activeAuth;
}
