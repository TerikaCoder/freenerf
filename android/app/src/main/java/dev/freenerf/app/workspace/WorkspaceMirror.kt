package dev.freenerf.app.workspace

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import java.io.File

/**
 * The agent always works inside the app-private workspace. Storage Access
 * Framework trees are imported into it and exported back explicitly, which is
 * the standard way to expose user files to a sandboxed Android process without
 * broad storage permissions.
 */
class WorkspaceMirror(private val context: Context, val root: File) {
    fun ensure(): File {
        if (!root.exists()) root.mkdirs()
        File(root, ".freenerf/uploads").mkdirs()
        return root
    }

    fun importTree(treeUri: Uri): Result<Int> = runCatching {
        ensure()
        val tree = DocumentFile.fromTreeUri(context, treeUri)
            ?: error("Could not open the selected folder")
        var copied = 0
        copyDirectory(tree, root) { copied++ }
        copied
    }

    fun exportTree(treeUri: Uri): Result<Int> = runCatching {
        ensure()
        val tree = DocumentFile.fromTreeUri(context, treeUri)
            ?: error("Could not open the selected folder")
        var copied = 0
        exportDirectory(root, tree, skipData = true) { copied++ }
        copied
    }

    /** Copies a single picked document into the workspace and returns its relative path. */
    fun importDocument(uri: Uri, folder: String = ".freenerf/uploads"): Result<String> = runCatching {
        ensure()
        val document = DocumentFile.fromSingleUri(context, uri)
            ?: error("Could not read the selected file")
        val name = uniqueName(document.name ?: "attachment")
        val directory = File(root, folder).apply { mkdirs() }
        val target = File(directory, name)
        context.contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "Could not open the selected file" }
            target.outputStream().use { output -> input.copyTo(output) }
        }
        target.relativeTo(root).path
    }

    fun readText(uri: Uri, limit: Int = 2_000_000): Result<String> = runCatching {
        context.contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "Could not open the selected file" }
            input.bufferedReader().readText().take(limit)
        }
    }

    private fun copyDirectory(source: DocumentFile, target: File, onFile: () -> Unit) {
        for (child in source.listFiles()) {
            val name = child.name ?: continue
            if (name == ".freenerf") continue
            val destination = File(target, name)
            if (child.isDirectory) {
                destination.mkdirs()
                copyDirectory(child, destination, onFile)
            } else {
                context.contentResolver.openInputStream(child.uri).use { input ->
                    if (input != null) {
                        destination.outputStream().use { output -> input.copyTo(output) }
                        onFile()
                    }
                }
            }
        }
    }

    private fun exportDirectory(source: File, target: DocumentFile, skipData: Boolean, onFile: () -> Unit) {
        for (child in source.listFiles().orEmpty()) {
            if (skipData && child.name == ".freenerf") continue
            if (child.isDirectory) {
                val directory = target.findFile(child.name)?.takeIf { it.isDirectory }
                    ?: target.createDirectory(child.name)
                if (directory != null) exportDirectory(child, directory, skipData = false, onFile)
            } else {
                val existing = target.findFile(child.name)
                existing?.delete()
                val document = target.createFile("application/octet-stream", child.name) ?: continue
                context.contentResolver.openOutputStream(document.uri).use { output ->
                    if (output != null) {
                        child.inputStream().use { input -> input.copyTo(output) }
                        onFile()
                    }
                }
            }
        }
    }

    private fun uniqueName(name: String): String {
        val directory = File(root, ".freenerf/uploads")
        var candidate = name
        var index = 1
        while (File(directory, candidate).exists()) {
            val dot = name.lastIndexOf('.')
            candidate = if (dot > 0) {
                "${name.substring(0, dot)}-$index${name.substring(dot)}"
            } else {
                "$name-$index"
            }
            index++
        }
        return candidate
    }
}
