import type { FirebaseOptions } from "firebase/app";
import type { FirebaseServerConfig } from "./serverConfig";

export function firebaseOptions(config: FirebaseServerConfig): FirebaseOptions {
  if (config.webAppId === null || config.webAppId.trim() === "") {
    throw new Error("This server is missing its public Firebase web app configuration.");
  }
  return {
    apiKey: config.apiKey,
    appId: config.webAppId,
    authDomain: config.authDomain ?? `${config.projectId}.firebaseapp.com`,
    messagingSenderId: config.messagingSenderId,
    projectId: config.projectId,
    storageBucket: config.storageBucket ?? undefined,
  };
}
