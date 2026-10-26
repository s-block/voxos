import type { BrandLogo } from "./api";
import { publicRuntimeConfig } from "./runtimeConfig";

const LOGO_DEV_IMAGE_ORIGIN = "https://img.logo.dev";
const HOSTNAME_PATTERN =
  /^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const DEFAULT_LOGO_SIZE = 64;

export const WHATSAPP_LOGO: BrandLogo = {
  domain: "whatsapp.com",
  label: "WhatsApp",
  source: "domain",
};

export type LogoImageOptions = {
  format?: "png" | "jpg" | "webp";
  size?: number;
};

export function logoImageUrl(
  logo: BrandLogo | null | undefined,
  options: LogoImageOptions = {},
): string | null {
  const token = (
    publicRuntimeConfig.logoDevPublishableKey ??
    import.meta.env.VITE_LOGODEV_PUBLISHABLE_KEY
  )?.trim();
  if (logo === null || logo === undefined || token === undefined || token === "") {
    return null;
  }

  const domain = normalizedLogoDomain(logo.domain);
  if (domain === null) {
    return null;
  }

  const size = boundedLogoSize(options.size ?? DEFAULT_LOGO_SIZE);
  const params = new URLSearchParams({
    format: options.format ?? "png",
    size: size.toString(),
    token,
  });
  return `${LOGO_DEV_IMAGE_ORIGIN}/${domain}?${params.toString()}`;
}

export function logoInitials(label: string): string {
  const words = label
    .trim()
    .split(/[\s._-]+/)
    .map((word) => word.replace(/[^a-z0-9]/gi, ""))
    .filter((word) => word !== "");
  if (words.length === 0) {
    return "?";
  }
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase();
  }
  return `${words[0][0]}${words[1][0]}`.toUpperCase();
}

function normalizedLogoDomain(domain: string): string | null {
  const normalized = domain.trim().toLowerCase().replace(/^www\./, "");
  return HOSTNAME_PATTERN.test(normalized) ? normalized : null;
}

function boundedLogoSize(size: number): number {
  if (!Number.isFinite(size)) {
    return DEFAULT_LOGO_SIZE;
  }
  return Math.min(800, Math.max(16, Math.round(size)));
}
