package dev.freenerf.app.net

import com.jcraft.jsch.ChannelExec
import com.jcraft.jsch.ChannelSftp
import com.jcraft.jsch.JSch
import com.jcraft.jsch.Session
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.Properties

/** Connection details for the optional remote host. */
data class SshConfig(
    val host: String = "",
    val port: Int = 22,
    val user: String = "",
    val password: String = "",
    val privateKey: String = "",
    val passphrase: String = "",
    val remoteDir: String = "",
) {
    val configured: Boolean
        get() = host.isNotBlank() && user.isNotBlank() &&
            (password.isNotBlank() || privateKey.isNotBlank())

    val label: String
        get() = if (host.isBlank()) "not configured" else "$user@$host:$port"
}

/**
 * Small SSH client used by the Remote settings tab and by the agent's
 * `ssh_exec` / `ssh_read_file` / `ssh_write_file` / `ssh_list_files` tools.
 *
 * Uses JSch (mwiede fork). The agent runtime calls [execute] through the JNI
 * bridge with a JSON request and gets a JSON envelope back.
 */
object SshClient {
    private const val CONNECT_TIMEOUT_MS = 15_000
    private const val MAX_OUTPUT = 40_000

    fun test(config: SshConfig): String = run(config) { session ->
        val out = runCommand(session, "pwd && uname -a 2>/dev/null || pwd", 15_000)
        "Connected to ${config.label}\n$out"
    }

    fun execute(config: SshConfig, request: JSONObject): JSONObject = try {
        val op = request.optString("op")
        val path = request.optString("path")
        val output = when (op) {
            "exec" -> {
                val command = request.optString("command")
                require(command.isNotBlank()) { "A command is required" }
                val timeout = request.optLong("timeout", 120).coerceIn(1, 600) * 1000
                run(config) { session -> runCommand(session, command, timeout) }
            }
            "read_file" -> {
                require(path.isNotBlank()) { "A path is required" }
                run(config) { session -> readFile(session, resolve(config, path)) }
            }
            "write_file" -> {
                require(path.isNotBlank()) { "A path is required" }
                run(config) { session ->
                    writeFile(session, resolve(config, path), request.optString("content"))
                }
            }
            "list_files" -> {
                val target = if (path.isBlank()) config.remoteDir else resolve(config, path)
                run(config) { session -> list(session, target.ifBlank { "." }) }
            }
            else -> error("Unknown SSH operation: $op")
        }
        JSONObject().put("ok", true).put("output", output.take(MAX_OUTPUT))
    } catch (error: Throwable) {
        JSONObject()
            .put("ok", false)
            .put("error", error.message ?: error.javaClass.simpleName)
    }

    /** Resolves a possibly relative path against the configured remote directory. */
    fun resolve(config: SshConfig, path: String): String {
        val value = path.trim()
        if (value.startsWith("/") || value.startsWith("~")) return value
        val base = config.remoteDir.trim().trimEnd('/')
        if (base.isEmpty()) return value
        return "$base/$value"
    }

    private fun <T> run(config: SshConfig, block: (Session) -> T): T {
        require(config.configured) {
            "No SSH host is configured. Add one in Settings → SSH."
        }
        var session: Session? = null
        try {
            val jsch = JSch()
            if (config.privateKey.isNotBlank()) {
                val name = "freenerf-${config.host}"
                jsch.addIdentity(
                    name,
                    config.privateKey.trim().toByteArray(Charsets.UTF_8),
                    null,
                    config.passphrase.takeIf { it.isNotBlank() }?.toByteArray(Charsets.UTF_8),
                )
            }
            session = jsch.getSession(config.user.trim(), config.host.trim(), config.port)
            if (config.password.isNotBlank()) session.setPassword(config.password)
            val props = Properties().apply {
                put("StrictHostKeyChecking", "no")
                put("PreferredAuthentications", "publickey,password,keyboard-interactive")
            }
            session.setConfig(props)
            session.connect(CONNECT_TIMEOUT_MS)
            return block(session)
        } finally {
            runCatching { session?.disconnect() }
        }
    }

    private fun runCommand(session: Session, command: String, timeoutMs: Long): String {
        val channel = session.openChannel("exec") as ChannelExec
        val stdout = ByteArrayOutputStream()
        val stderr = ByteArrayOutputStream()
        channel.setErrStream(stderr)
        channel.setCommand(command)
        channel.connect(CONNECT_TIMEOUT_MS)
        session.timeout = (timeoutMs / 1000).toInt().coerceAtLeast(1)
        try {
            channel.inputStream.copyTo(stdout)
            while (!channel.isClosed) Thread.sleep(20)
        } finally {
            session.timeout = 0
        }
        val code = channel.exitStatus
        channel.disconnect()
        val out = stdout.toString(Charsets.UTF_8.name())
        val err = stderr.toString(Charsets.UTF_8.name())
        val body = buildString {
            append("Exit code: $code\n")
            append(out)
            if (err.isNotBlank()) {
                if (out.isNotBlank() && !out.endsWith("\n")) append('\n')
                append(err)
            }
        }
        return body.trimEnd().ifEmpty { "Exit code: $code" }
    }

    private fun readFile(session: Session, path: String): String {
        val sftp = session.openChannel("sftp") as ChannelSftp
        sftp.connect(CONNECT_TIMEOUT_MS)
        try {
            val bytes = sftp.get(path).use { it.readBytes() }
            val text = bytes.toString(Charsets.UTF_8)
            return "Read ${bytes.size} bytes from $path\n$text"
        } finally {
            sftp.disconnect()
        }
    }

    private fun writeFile(session: Session, path: String, content: String): String {
        val sftp = session.openChannel("sftp") as ChannelSftp
        sftp.connect(CONNECT_TIMEOUT_MS)
        try {
            val bytes = content.toByteArray(Charsets.UTF_8)
            ByteArrayInputStream(bytes).use { sftp.put(it, path) }
            return "Wrote ${bytes.size} bytes to $path"
        } finally {
            sftp.disconnect()
        }
    }

    private fun list(session: Session, path: String): String {
        val sftp = session.openChannel("sftp") as ChannelSftp
        sftp.connect(CONNECT_TIMEOUT_MS)
        try {
            val entries = sftp.ls(path)
            val lines = buildList {
                for (entry in entries) {
                    val name = entry.filename
                    if (name == "." || name == "..") continue
                    val kind = if (entry.attrs.isDir) "dir " else "file"
                    val size = if (entry.attrs.isDir) "-" else entry.attrs.size.toString()
                    add("$kind ${size.padStart(10)}  $name")
                }
            }.sorted()
            if (lines.isEmpty()) return "Directory $path is empty"
            return "Contents of $path:\n" + lines.joinToString("\n")
        } finally {
            sftp.disconnect()
        }
    }
}
