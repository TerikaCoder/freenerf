package dev.freenerf.app.storage

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.Settings
import androidx.core.content.ContextCompat
import java.io.File

/**
 * Shared-storage access for the workspace picker.
 *
 * Android 11+ needs "All files access" to read an arbitrary folder under
 * /sdcard; Android 10 and older use the READ/WRITE_EXTERNAL_STORAGE runtime
 * permission. Both are requested from Settings → Workspace.
 */
object StorageAccess {
    fun hasAccess(context: Context): Boolean = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> Environment.isExternalStorageManager()
        else -> ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.READ_EXTERNAL_STORAGE,
        ) == PackageManager.PERMISSION_GRANTED
    }

    /** Intent that lets the user grant broad file access, when the OS offers one. */
    fun requestIntent(context: Context): Intent? = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.R -> Intent(
            Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
            Uri.parse("package:${context.packageName}"),
        )
        else -> null
    }

    /**
     * Directories the browser can start in. Shared storage comes first so the
     * picker opens on /sdcard as requested; app-private storage is always
     * available as a fallback.
     */
    fun roots(context: Context): List<File> {
        val candidates = listOf(
            Environment.getExternalStorageDirectory(),
            File("/storage/emulated/0"),
            File("/sdcard"),
            context.getExternalFilesDir(null),
            context.filesDir,
        )
        val seen = mutableSetOf<String>()
        return candidates.filterNotNull().filter { dir ->
            val path = dir.absolutePath
            seen.add(path)
        }.distinctBy { it.absolutePath }
    }

    /** True when [path] is outside the app sandbox and needs All files access. */
    fun needsStoragePermission(context: Context, path: String): Boolean {
        val sandbox = context.filesDir.absolutePath
        return !path.startsWith(sandbox) && !hasAccess(context)
    }
}
