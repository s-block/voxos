export type PublicRuntimeConfig = {
  logoDevPublishableKey?: string;
  sentryDsn?: string;
  sentryEnvironment?: string;
  sentryRelease?: string;
  sentryTracesSampleRate?: number;
};

declare global {
  interface Window {
    __VOXOS_CONFIG__?: PublicRuntimeConfig;
  }
}

export const publicRuntimeConfig: Readonly<PublicRuntimeConfig> = Object.freeze(
  window.__VOXOS_CONFIG__ ?? {},
);
