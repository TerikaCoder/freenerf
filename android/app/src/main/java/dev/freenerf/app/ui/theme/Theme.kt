package dev.freenerf.app.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import dev.freenerf.app.data.AppSettings
import kotlin.math.atan2
import kotlin.math.cbrt
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.pow
import kotlin.math.sin

/**
 * Material You colour, ported from the Theme Studio web UI.
 *
 * A complete MD3 scheme is derived from one seed colour. Tones use the CIE L*
 * lightness scale mapped into OKLCH, with tonal-palette chroma curves for the
 * primary, secondary, tertiary, neutral and neutral-variant families. Roles
 * follow the Material 3 light/dark tone mapping, so every on/container pairing
 * keeps its intended contrast and no baseline purple leaks into the app.
 *
 * Everything here is a pure function of the seed: the whole palette is cached
 * by [FreeNerfTheme] and only recomputed when a theme setting actually changes.
 */

/** The six Theme Studio seeds, shared with the terminal app and the web UI. */
data class ThemeSeed(val id: String, val label: String, val hex: String)

val THEME_SEEDS: List<ThemeSeed> = listOf(
    ThemeSeed("verdant", "Verdant", "#2E7D4F"),
    ThemeSeed("violet", "Violet", "#6750A4"),
    ThemeSeed("ocean", "Ocean", "#00658F"),
    ThemeSeed("amber", "Amber", "#7A5900"),
    ThemeSeed("rose", "Rose", "#984061"),
    ThemeSeed("graphite", "Graphite", "#46525C"),
)

fun themeSeedHex(name: String): String =
    THEME_SEEDS.firstOrNull { it.id == name }?.hex ?: "#2E7D4F"

/** Swatch colour for the Theme Studio accent picker. */
fun themeAccentColor(name: String): Color =
    parseHexColor(themeSeedHex(name)) ?: Color(0xFF2E7D4F)

/** Parses "#RRGGBB" or "RRGGBB" into a colour; null when the text is not one. */
fun parseHexColor(value: String): Color? {
    val raw = value.trim().removePrefix("#")
    if (raw.length != 6) return null
    if (raw.any { it !in "0123456789abcdefABCDEF" }) return null
    val number = raw.toLongOrNull(16) ?: return null
    return Color(0xFF000000L or number)
}

/** "#RRGGBB" for a colour, used by previews and the Theme Studio. */
fun hexString(color: Color): String {
    fun channel(value: Float): Int = (value.coerceIn(0f, 1f) * 255f + 0.5f).toInt()
    return "#%02X%02X%02X".format(channel(color.red), channel(color.green), channel(color.blue))
}

// ----------------------------------------------------------------------
// OKLCH
// ----------------------------------------------------------------------

/** Chroma multiplier by tone: the shape of an MD3 tonal palette. */
private val CURVE_TONES = doubleArrayOf(
    0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 95.0, 100.0,
)
private val CURVE_CHROMA = doubleArrayOf(
    0.0, 0.34, 0.62, 0.82, 0.96, 1.0, 0.96, 0.82, 0.62, 0.38, 0.24, 0.0,
)

private fun chromaFactor(tone: Double): Double {
    if (tone <= 0.0 || tone >= 100.0) return 0.0
    for (index in 1 until CURVE_TONES.size) {
        if (tone <= CURVE_TONES[index]) {
            val t0 = CURVE_TONES[index - 1]
            val t1 = CURVE_TONES[index]
            val c0 = CURVE_CHROMA[index - 1]
            val c1 = CURVE_CHROMA[index]
            return c0 + (c1 - c0) * (tone - t0) / (t1 - t0)
        }
    }
    return 0.0
}

/** CIE L* tone -> OKLCH lightness (Y for a neutral, then cbrt for OKLab L). */
private fun toneLightness(tone: Double): Double {
    val t = tone.coerceIn(0.0, 100.0)
    val y = if (t <= 8.0) t / 903.3 else ((t + 16.0) / 116.0).pow(3)
    return cbrt(y) * 100.0
}

private fun srgbToLinear(channel: Double): Double =
    if (channel <= 0.04045) channel / 12.92 else ((channel + 0.055) / 1.055).pow(2.4)

private fun linearToSrgb(linear: Double): Double {
    val value = linear.coerceIn(0.0, 1.0)
    return if (value <= 0.0031308) value * 12.92 else 1.055 * value.pow(1.0 / 2.4) - 0.055
}

private fun oklchToColor(lightness: Double, chroma: Double, hue: Double): Color {
    val l = lightness / 100.0
    val radians = Math.toRadians(hue)
    val a = chroma * cos(radians)
    val b = chroma * sin(radians)

    val lPrime = l + 0.3963377774 * a + 0.2158037573 * b
    val mPrime = l - 0.1055613458 * a - 0.0638541728 * b
    val sPrime = l - 0.0894841775 * a - 1.2914855480 * b

    val lp = lPrime * lPrime * lPrime
    val mp = mPrime * mPrime * mPrime
    val sp = sPrime * sPrime * sPrime

    val red = 4.0767416621 * lp - 3.3077115913 * mp + 0.2309699292 * sp
    val green = -1.2684380046 * lp + 2.6097574011 * mp - 0.3413193965 * sp
    val blue = -0.0041960863 * lp - 0.7034186147 * mp + 1.7076147010 * sp

    return Color(
        red = linearToSrgb(red).toFloat(),
        green = linearToSrgb(green).toFloat(),
        blue = linearToSrgb(blue).toFloat(),
        alpha = 1f,
    )
}

/** Hue (degrees) and chroma of a colour in OKLCH. */
private fun hueAndChroma(color: Color): Pair<Double, Double> {
    val r = srgbToLinear(color.red.toDouble())
    val g = srgbToLinear(color.green.toDouble())
    val b = srgbToLinear(color.blue.toDouble())

    val l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    val m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    val s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

    val lp = cbrt(l)
    val mp = cbrt(m)
    val sp = cbrt(s)

    val a = 1.9779984951 * lp - 2.428592205 * mp + 0.4505937099 * sp
    val bb = 0.0259040371 * lp + 0.7827717662 * mp - 0.808675766 * sp

    var hue = Math.toDegrees(atan2(bb, a))
    if (hue < 0) hue += 360.0
    return hue to hypot(a, bb)
}

/** One tonal family: a fixed hue, with chroma following the MD3 tone curve. */
private fun tonal(hue: Double, chroma: Double, tone: Double): Color =
    oklchToColor(toneLightness(tone), (chroma * chromaFactor(tone)).coerceAtLeast(0.0), hue)

private fun contrastStep(level: String): Int = when (level) {
    "medium" -> 1
    "high" -> 2
    else -> 0
}

private fun pick(values: IntArray, step: Int): Double = values[step.coerceIn(0, values.size - 1)].toDouble()

private val ACCENT_TONES = intArrayOf(40, 36, 30)
private val CONTAINER_TONES = intArrayOf(90, 88, 86)
private val ON_CONTAINER_TONES = intArrayOf(10, 8, 6)
private val ON_SURFACE_TONES = intArrayOf(10, 8, 6)
private val ON_SURFACE_VARIANT_TONES = intArrayOf(30, 27, 22)
private val OUTLINE_TONES = intArrayOf(50, 46, 38)
private val OUTLINE_VARIANT_TONES = intArrayOf(80, 78, 76)
private val DARK_ACCENT_TONES = intArrayOf(80, 84, 88)
private val DARK_CONTAINER_TONES = intArrayOf(30, 32, 36)
private val DARK_ON_CONTAINER_TONES = intArrayOf(90, 92, 96)
private val DARK_ON_SURFACE_TONES = intArrayOf(90, 92, 96)
private val DARK_ON_SURFACE_VARIANT_TONES = intArrayOf(80, 84, 90)
private val DARK_OUTLINE_TONES = intArrayOf(60, 66, 74)
private val DARK_OUTLINE_VARIANT_TONES = intArrayOf(30, 32, 36)

/**
 * Builds the full Material You scheme for [seedHex]. Pure and cheap enough to
 * run whenever the seed, mode, contrast or AMOLED setting changes.
 */
fun materialScheme(
    seedHex: String,
    dark: Boolean,
    amoled: Boolean,
    contrast: String = "standard",
): ColorScheme {
    val seed = parseHexColor(seedHex) ?: parseHexColor("#2E7D4F") ?: Color(0xFF2E7D4F)
    val (hue, chroma) = hueAndChroma(seed)
    val primaryChroma = chroma.coerceIn(0.02, 0.16)
    val secondaryChroma = (chroma * 0.34).coerceIn(0.008, 0.05)
    val tertiaryHue = (hue + 62.0) % 360.0
    val tertiaryChroma = (chroma * 0.8).coerceIn(0.02, 0.12)
    val neutralChroma = (chroma * 0.055).coerceAtMost(0.012)
    val neutralVariantChroma = (chroma * 0.16).coerceAtMost(0.03)
    val errorHue = 27.0
    val errorChroma = 0.19

    fun primary(tone: Double) = tonal(hue, primaryChroma, tone)
    fun secondary(tone: Double) = tonal(hue, secondaryChroma, tone)
    fun tertiary(tone: Double) = tonal(tertiaryHue, tertiaryChroma, tone)
    fun neutral(tone: Double) = tonal(hue, neutralChroma, tone)
    fun neutralVariant(tone: Double) = tonal(hue, neutralVariantChroma, tone)
    fun error(tone: Double) = tonal(errorHue, errorChroma, tone)

    val step = contrastStep(contrast)
    val accentTone = pick(ACCENT_TONES, step)
    val containerTone = pick(CONTAINER_TONES, step)
    val onContainerTone = pick(ON_CONTAINER_TONES, step)
    val onSurfaceTone = pick(ON_SURFACE_TONES, step)
    val onSurfaceVariantTone = pick(ON_SURFACE_VARIANT_TONES, step)
    val outlineTone = pick(OUTLINE_TONES, step)
    val outlineVariantTone = pick(OUTLINE_VARIANT_TONES, step)
    val darkAccentTone = pick(DARK_ACCENT_TONES, step)
    val darkContainerTone = pick(DARK_CONTAINER_TONES, step)
    val darkOnContainerTone = pick(DARK_ON_CONTAINER_TONES, step)
    val darkOnSurfaceTone = pick(DARK_ON_SURFACE_TONES, step)
    val darkOnSurfaceVariantTone = pick(DARK_ON_SURFACE_VARIANT_TONES, step)
    val darkOutlineTone = pick(DARK_OUTLINE_TONES, step)
    val darkOutlineVariantTone = pick(DARK_OUTLINE_VARIANT_TONES, step)

    return if (dark) {
        val surfaceTone = if (amoled) 0.0 else 6.0
        darkColorScheme(
            primary = primary(darkAccentTone),
            onPrimary = primary(20.0),
            primaryContainer = primary(darkContainerTone),
            onPrimaryContainer = primary(darkOnContainerTone),
            inversePrimary = primary(40.0),
            secondary = secondary(darkAccentTone),
            onSecondary = secondary(20.0),
            secondaryContainer = secondary(darkContainerTone),
            onSecondaryContainer = secondary(darkOnContainerTone),
            tertiary = tertiary(darkAccentTone),
            onTertiary = tertiary(20.0),
            tertiaryContainer = tertiary(darkContainerTone),
            onTertiaryContainer = tertiary(darkOnContainerTone),
            error = error(80.0),
            onError = error(20.0),
            errorContainer = error(30.0),
            onErrorContainer = error(90.0),
            background = neutral(surfaceTone),
            onBackground = neutral(darkOnSurfaceTone),
            surface = neutral(surfaceTone),
            onSurface = neutral(darkOnSurfaceTone),
            surfaceVariant = neutralVariant(30.0),
            onSurfaceVariant = neutralVariant(darkOnSurfaceVariantTone),
            surfaceTint = primary(darkAccentTone),
            inverseSurface = neutral(90.0),
            inverseOnSurface = neutral(20.0),
            outline = neutralVariant(darkOutlineTone),
            outlineVariant = neutralVariant(darkOutlineVariantTone),
            surfaceDim = neutral(if (amoled) 0.0 else 6.0),
            surfaceBright = neutral(if (amoled) 12.0 else 24.0),
            surfaceContainerLowest = neutral(if (amoled) 0.0 else 4.0),
            surfaceContainerLow = neutral(if (amoled) 2.0 else 10.0),
            surfaceContainer = neutral(if (amoled) 3.0 else 12.0),
            surfaceContainerHigh = neutral(if (amoled) 5.0 else 17.0),
            surfaceContainerHighest = neutral(if (amoled) 8.0 else 22.0),
            scrim = neutral(0.0),
        )
    } else {
        lightColorScheme(
            primary = primary(accentTone),
            onPrimary = primary(100.0),
            primaryContainer = primary(containerTone),
            onPrimaryContainer = primary(onContainerTone),
            inversePrimary = primary(80.0),
            secondary = secondary(accentTone),
            onSecondary = secondary(100.0),
            secondaryContainer = secondary(containerTone),
            onSecondaryContainer = secondary(onContainerTone),
            tertiary = tertiary(accentTone),
            onTertiary = tertiary(100.0),
            tertiaryContainer = tertiary(containerTone),
            onTertiaryContainer = tertiary(onContainerTone),
            error = error(40.0),
            onError = error(100.0),
            errorContainer = error(90.0),
            onErrorContainer = error(10.0),
            background = neutral(98.0),
            onBackground = neutral(onSurfaceTone),
            surface = neutral(98.0),
            onSurface = neutral(onSurfaceTone),
            surfaceVariant = neutralVariant(90.0),
            onSurfaceVariant = neutralVariant(onSurfaceVariantTone),
            surfaceTint = primary(accentTone),
            inverseSurface = neutral(20.0),
            inverseOnSurface = neutral(95.0),
            outline = neutralVariant(outlineTone),
            outlineVariant = neutralVariant(outlineVariantTone),
            surfaceDim = neutral(87.0),
            surfaceBright = neutral(100.0),
            surfaceContainerLowest = neutral(100.0),
            surfaceContainerLow = neutral(96.0),
            surfaceContainer = neutral(94.0),
            surfaceContainerHigh = neutral(92.0),
            surfaceContainerHighest = neutral(90.0),
            scrim = neutral(0.0),
        )
    }
}

// ----------------------------------------------------------------------
// Type, shape and motion
// ----------------------------------------------------------------------

/** Expressive type: heavier display/title roles, roomier reading line heights. */
private val AppTypography = Typography().run {
    copy(
        displaySmall = displaySmall.copy(fontWeight = FontWeight.SemiBold, letterSpacing = (-0.5).sp),
        headlineMedium = headlineMedium.copy(fontWeight = FontWeight.SemiBold),
        headlineSmall = headlineSmall.copy(fontWeight = FontWeight.SemiBold),
        titleLarge = titleLarge.copy(fontWeight = FontWeight.SemiBold),
        titleMedium = titleMedium.copy(fontWeight = FontWeight.Medium),
        bodyLarge = bodyLarge.copy(lineHeight = 24.sp),
        bodyMedium = bodyMedium.copy(lineHeight = 21.sp),
        labelLarge = labelLarge.copy(letterSpacing = 0.2.sp),
        labelSmall = labelSmall.copy(letterSpacing = 0.5.sp),
    )
}

/** Material You rounding: generous corners on cards, fields and sheets. */
private val AppShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
    extraLarge = RoundedCornerShape(32.dp),
)

@Composable
fun FreeNerfTheme(
    settings: AppSettings = AppSettings(),
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val systemDark = isSystemInDarkTheme()
    val darkTheme = when (settings.themeMode) {
        "light" -> false
        "dark" -> true
        else -> systemDark
    }
    val custom = parseHexColor(settings.customAccent)
    val seedHex = if (custom != null) settings.customAccent.trim() else themeSeedHex(settings.themeAccent)
    val useDynamic = settings.dynamicColor &&
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
        !settings.amoledBlack

    // One scheme build per actual theme change; recomposition reuses it.
    val scheme = remember(
        seedHex,
        darkTheme,
        settings.amoledBlack,
        settings.themeContrast,
        useDynamic,
        context,
    ) {
        when {
            useDynamic ->
                if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
            else -> materialScheme(
                seedHex = seedHex,
                dark = darkTheme,
                amoled = settings.amoledBlack,
                contrast = settings.themeContrast,
            )
        }
    }

    MaterialTheme(
        colorScheme = scheme,
        typography = AppTypography,
        shapes = AppShapes,
        content = content,
    )
}
