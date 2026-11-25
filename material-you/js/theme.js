/* ==========================================================================
   Material You colour
   Derives a complete MD3 scheme from one seed colour. Tones use the CIE L*
   lightness scale mapped into OKLCH (a perceptual space CSS renders natively),
   with tonal-palette chroma curves for primary, secondary, tertiary, neutral
   and neutral-variant. Roles follow the Material 3 light/dark tone mapping, so
   every on/container pairing keeps its intended contrast.
   ========================================================================== */

export const SEEDS = Object.freeze({
  verdant: "#2E7D4F",
  violet: "#6750A4",
  ocean: "#00658F",
  amber: "#7A5900",
  rose: "#984061",
  graphite: "#46525C",
});

export const DEFAULTS = Object.freeze({
  mode: "system",
  seed: "verdant",
  custom: "",
  contrast: "standard",
  amoled: false,
});

const CONTRAST = { standard: 0, medium: 1, high: 2 };

/* Chroma multiplier by tone — the shape of an MD3 tonal palette. */
const CHROMA_CURVE = [
  [0, 0], [10, 0.34], [20, 0.62], [30, 0.82], [40, 0.96], [50, 1],
  [60, 0.96], [70, 0.82], [80, 0.62], [90, 0.38], [95, 0.24], [100, 0],
];

const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export function isHex(value) {
  return /^#?[0-9a-f]{6}$/i.test(String(value ?? "").trim());
}

export function normalizeHex(value) {
  if (!isHex(value)) return null;
  return `#${String(value).trim().replace(/^#/, "").toUpperCase()}`;
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function srgbToLinear(channel) {
  const v = channel / 255;
  return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
}

function rgbToOklch([r, g, b]) {
  const lr = srgbToLinear(r);
  const lg = srgbToLinear(g);
  const lb = srgbToLinear(b);

  const l = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb;
  const m = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb;
  const s = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb;

  const l_ = Math.cbrt(l);
  const m_ = Math.cbrt(m);
  const s_ = Math.cbrt(s);

  const L = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
  const A = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
  const B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;

  let H = (Math.atan2(B, A) * 180) / Math.PI;
  if (H < 0) H += 360;
  return { L, C: Math.hypot(A, B), H };
}

/* CIE L* tone -> OKLCH lightness (Y for a neutral, then cbrt for OKLab L). */
function toneLightness(tone) {
  const t = clamp(tone, 0, 100);
  const y = t <= 8 ? t / 903.3 : Math.pow((t + 16) / 116, 3);
  return Math.cbrt(y) * 100;
}

function chromaFactor(tone) {
  for (let i = 1; i < CHROMA_CURVE.length; i += 1) {
    const [t1, c1] = CHROMA_CURVE[i];
    if (tone <= t1) {
      const [t0, c0] = CHROMA_CURVE[i - 1];
      return c0 + ((c1 - c0) * (tone - t0)) / (t1 - t0);
    }
  }
  return 0;
}

function family(hue, chroma) {
  const h = ((hue % 360) + 360) % 360;
  return (tone) => {
    const l = toneLightness(tone);
    const c = Math.max(0, chroma * chromaFactor(tone));
    return `oklch(${l.toFixed(2)}% ${c.toFixed(4)} ${h.toFixed(1)})`;
  };
}

const kebab = (name) => name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);

export function systemPrefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

/**
 * Applies a Material You scheme to :root. Returns the resolved brightness and
 * the seed actually used, so the UI can label the Theme Studio accurately.
 */
export function applyScheme(options = {}) {
  const theme = { ...DEFAULTS, ...options };
  const seedHex = normalizeHex(theme.custom) || SEEDS[theme.seed] || SEEDS.verdant;
  const { C, H } = rgbToOklch(hexToRgb(seedHex));
  const dark = theme.mode === "dark" || (theme.mode === "system" && systemPrefersDark());
  const amoled = dark && Boolean(theme.amoled);
  const step = CONTRAST[theme.contrast] ?? 0;

  const primary = family(H, clamp(C, 0.02, 0.16));
  const secondary = family(H, clamp(C * 0.34, 0.008, 0.05));
  const tertiary = family(H + 62, clamp(C * 0.8, 0.02, 0.12));
  const neutral = family(H, Math.min(C * 0.055, 0.012));
  const neutralVariant = family(H, Math.min(C * 0.16, 0.03));
  const error = family(27, 0.19);

  const accentTone = [40, 36, 30][step];
  const containerTone = [90, 88, 86][step];
  const onContainerTone = [10, 8, 6][step];
  const onSurfaceTone = [10, 8, 6][step];
  const onSurfaceVariantTone = [30, 27, 22][step];
  const outlineTone = [50, 46, 38][step];
  const outlineVariantTone = [80, 78, 76][step];
  const darkAccentTone = [80, 84, 88][step];
  const darkContainerTone = [30, 32, 36][step];
  const darkOnContainerTone = [90, 92, 96][step];
  const darkOnSurfaceTone = [90, 92, 96][step];
  const darkOnSurfaceVariantTone = [80, 84, 90][step];
  const darkOutlineTone = [60, 66, 74][step];
  const darkOutlineVariantTone = [30, 32, 36][step];

  let roles;
  if (!dark) {
    roles = {
      primary: primary(accentTone),
      onPrimary: primary(100),
      primaryContainer: primary(containerTone),
      onPrimaryContainer: primary(onContainerTone),
      inversePrimary: primary(80),
      secondary: secondary(accentTone),
      onSecondary: secondary(100),
      secondaryContainer: secondary(containerTone),
      onSecondaryContainer: secondary(onContainerTone),
      tertiary: tertiary(accentTone),
      onTertiary: tertiary(100),
      tertiaryContainer: tertiary(containerTone),
      onTertiaryContainer: tertiary(onContainerTone),
      error: error(40),
      onError: error(100),
      errorContainer: error(90),
      onErrorContainer: error(10),
      background: neutral(98),
      onBackground: neutral(onSurfaceTone),
      surface: neutral(98),
      onSurface: neutral(onSurfaceTone),
      surfaceVariant: neutralVariant(90),
      onSurfaceVariant: neutralVariant(onSurfaceVariantTone),
      surfaceDim: neutral(87),
      surfaceBright: neutral(100),
      surfaceContainerLowest: neutral(100),
      surfaceContainerLow: neutral(96),
      surfaceContainer: neutral(94),
      surfaceContainerHigh: neutral(92),
      surfaceContainerHighest: neutral(90),
      inverseSurface: neutral(20),
      inverseOnSurface: neutral(95),
      outline: neutralVariant(outlineTone),
      outlineVariant: neutralVariant(outlineVariantTone),
      scrim: neutral(0),
      shadow: neutral(0),
    };
  } else {
    const surfaceTone = amoled ? 0 : 6;
    roles = {
      primary: primary(darkAccentTone),
      onPrimary: primary(20),
      primaryContainer: primary(darkContainerTone),
      onPrimaryContainer: primary(darkOnContainerTone),
      inversePrimary: primary(40),
      secondary: secondary(darkAccentTone),
      onSecondary: secondary(20),
      secondaryContainer: secondary(darkContainerTone),
      onSecondaryContainer: secondary(darkOnContainerTone),
      tertiary: tertiary(darkAccentTone),
      onTertiary: tertiary(20),
      tertiaryContainer: tertiary(darkContainerTone),
      onTertiaryContainer: tertiary(darkOnContainerTone),
      error: error(80),
      onError: error(20),
      errorContainer: error(30),
      onErrorContainer: error(90),
      background: neutral(surfaceTone),
      onBackground: neutral(darkOnSurfaceTone),
      surface: neutral(surfaceTone),
      onSurface: neutral(darkOnSurfaceTone),
      surfaceVariant: neutralVariant(30),
      onSurfaceVariant: neutralVariant(darkOnSurfaceVariantTone),
      surfaceDim: neutral(amoled ? 0 : 6),
      surfaceBright: neutral(amoled ? 12 : 24),
      surfaceContainerLowest: neutral(amoled ? 0 : 4),
      surfaceContainerLow: neutral(amoled ? 2 : 10),
      surfaceContainer: neutral(amoled ? 3 : 12),
      surfaceContainerHigh: neutral(amoled ? 5 : 17),
      surfaceContainerHighest: neutral(amoled ? 8 : 22),
      inverseSurface: neutral(90),
      inverseOnSurface: neutral(20),
      outline: neutralVariant(darkOutlineTone),
      outlineVariant: neutralVariant(darkOutlineVariantTone),
      scrim: neutral(0),
      shadow: neutral(0),
    };
  }

  const root = document.documentElement;
  Object.entries(roles).forEach(([role, value]) => {
    root.style.setProperty(`--md-sys-color-${kebab(role)}`, value);
  });
  root.style.setProperty("--md-sys-color-surface-tint", roles.primary);
  root.dataset.theme = dark ? "dark" : "light";
  root.style.colorScheme = dark ? "dark" : "light";

  return { dark, seedHex, amoled };
}
