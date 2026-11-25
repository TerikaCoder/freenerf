package dev.freenerf.app.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

sealed interface MarkdownBlock {
    data class Paragraph(val text: String) : MarkdownBlock
    data class Heading(val level: Int, val text: String) : MarkdownBlock
    data class Code(val language: String, val code: String) : MarkdownBlock
    data class Bullets(val items: List<String>) : MarkdownBlock
    data class Quote(val text: String) : MarkdownBlock
    data class Table(
        val headers: List<String>,
        val rows: List<List<String>>,
        val alignments: List<TextAlign>,
    ) : MarkdownBlock
    data object Rule : MarkdownBlock
}

/** Small, dependency-free Markdown parser sufficient for assistant output. */
object Markdown {
    private val separator = Regex(
        "^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)+\\|?\\s*$",
    )

    private fun isTableStart(lines: List<String>, index: Int): Boolean {
        if (index + 1 >= lines.size) return false
        val header = lines[index]
        if (!header.contains('|')) return false
        return separator.matches(lines[index + 1].trim())
    }

    private fun splitRow(line: String): List<String> {
        var value = line.trim()
        if (value.startsWith("|")) value = value.substring(1)
        if (value.endsWith("|") && !value.endsWith("\\|")) {
            value = value.substring(0, value.length - 1)
        }
        val cells = mutableListOf<String>()
        val current = StringBuilder()
        var escaped = false
        for (character in value) {
            when {
                escaped -> {
                    current.append(character)
                    escaped = false
                }
                character == '\\' -> escaped = true
                character == '|' -> {
                    cells += current.toString().trim()
                    current.clear()
                }
                else -> current.append(character)
            }
        }
        cells += current.toString().trim()
        return cells
    }

    private fun alignment(cell: String): TextAlign {
        val value = cell.trim()
        val left = value.startsWith(":")
        val right = value.endsWith(":")
        return when {
            left && right -> TextAlign.Center
            right -> TextAlign.End
            else -> TextAlign.Start
        }
    }

    fun parse(markdown: String): List<MarkdownBlock> {
        val blocks = mutableListOf<MarkdownBlock>()
        val lines = markdown.replace("\r\n", "\n").split('\n')
        val paragraph = mutableListOf<String>()
        val bullets = mutableListOf<String>()

        fun flushParagraph() {
            if (paragraph.isNotEmpty()) {
                blocks += MarkdownBlock.Paragraph(paragraph.joinToString("\n").trim())
                paragraph.clear()
            }
        }

        fun flushBullets() {
            if (bullets.isNotEmpty()) {
                blocks += MarkdownBlock.Bullets(bullets.toList())
                bullets.clear()
            }
        }

        var index = 0
        while (index < lines.size) {
            val line = lines[index]
            val trimmed = line.trim()

            if (trimmed.startsWith("```")) {
                flushParagraph()
                flushBullets()
                val language = trimmed.removePrefix("```").trim()
                val code = mutableListOf<String>()
                index++
                while (index < lines.size && !lines[index].trim().startsWith("```")) {
                    code += lines[index]
                    index++
                }
                blocks += MarkdownBlock.Code(language, code.joinToString("\n"))
                index++
                continue
            }

            if (isTableStart(lines, index)) {
                flushParagraph()
                flushBullets()
                val headers = splitRow(lines[index])
                val alignments = splitRow(lines[index + 1]).map(::alignment)
                index += 2
                val rows = mutableListOf<List<String>>()
                while (index < lines.size) {
                    val candidate = lines[index].trim()
                    if (candidate.isEmpty() || !candidate.contains('|')) break
                    if (candidate.startsWith("```")) break
                    rows += splitRow(candidate)
                    index++
                }
                val columns = maxOf(headers.size, rows.maxOfOrNull { it.size } ?: 0)
                val paddedHeaders = List(columns) { headers.getOrElse(it) { "" } }
                val paddedRows = rows.map { row -> List(columns) { row.getOrElse(it) { "" } } }
                val paddedAlign = List(columns) {
                    alignments.getOrElse(it) { TextAlign.Start }
                }
                blocks += MarkdownBlock.Table(paddedHeaders, paddedRows, paddedAlign)
                continue
            }

            when {
                trimmed.isEmpty() -> {
                    flushParagraph()
                    flushBullets()
                }
                trimmed.startsWith("#") -> {
                    flushParagraph()
                    flushBullets()
                    val level = trimmed.takeWhile { it == '#' }.length.coerceIn(1, 6)
                    blocks += MarkdownBlock.Heading(level, trimmed.drop(level).trim())
                }
                trimmed == "---" || trimmed == "***" -> {
                    flushParagraph()
                    flushBullets()
                    blocks += MarkdownBlock.Rule
                }
                trimmed.startsWith("> ") -> {
                    flushParagraph()
                    flushBullets()
                    blocks += MarkdownBlock.Quote(trimmed.removePrefix("> ").trim())
                }
                trimmed.startsWith("- ") || trimmed.startsWith("* ") -> {
                    flushParagraph()
                    bullets += trimmed.substring(2).trim()
                }
                trimmed.matches(Regex("^\\d+[.)]\\s+.*")) -> {
                    flushParagraph()
                    bullets += trimmed.replaceFirst(Regex("^\\d+[.)]\\s+"), "")
                }
                else -> {
                    flushBullets()
                    paragraph += line
                }
            }
            index++
        }
        flushParagraph()
        flushBullets()
        return blocks
    }

    /**
     * Splits streaming Markdown at the last blank-line boundary that is not
     * inside an open code fence. Everything before it is complete and can be
     * parsed once; the tail is drawn as plain text until the message ends. A
     * long answer therefore stops being fully re-parsed and re-laid-out on
     * every token flush.
     */
    fun streamingSplit(text: String): Pair<String, String> {
        if (text.isBlank()) return "" to text
        val fences = Regex("(?m)^\\s*```")
        var index = text.lastIndexOf("\n\n")
        while (index > 0) {
            val prefix = text.substring(0, index)
            if (fences.findAll(prefix).count() % 2 == 0) {
                return prefix to text.substring(index)
            }
            index = text.lastIndexOf("\n\n", index - 1)
        }
        return "" to text
    }

    /** Renders `code`, **bold**, *italic*, ~~strikethrough~~ and [links](url). */
    fun inline(text: String): AnnotatedString = buildAnnotatedString {
        var cursor = 0
        val pattern = Regex(
            "`([^`]+)`|\\[([^\\]]+)]\\(([^)\\s]+)\\)|\\*\\*([^*]+)\\*\\*|\\*([^*]+)\\*|~~([^~]+)~~",
        )
        for (match in pattern.findAll(text)) {
            append(text.substring(cursor, match.range.first))
            val group = match.groups
            when {
                group[1] != null -> withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) {
                    append(group[1]!!.value)
                }
                group[2] != null -> withStyle(
                    SpanStyle(
                        fontWeight = FontWeight.Medium,
                        textDecoration = TextDecoration.Underline,
                        color = Color.Unspecified,
                    ),
                ) {
                    append(group[2]!!.value)
                }
                group[4] != null -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                    append(group[4]!!.value)
                }
                group[5] != null -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                    append(group[5]!!.value)
                }
                group[6] != null -> withStyle(
                    SpanStyle(textDecoration = TextDecoration.LineThrough),
                ) {
                    append(group[6]!!.value)
                }
            }
            cursor = match.range.last + 1
        }
        append(text.substring(cursor))
    }
}

@Composable
fun MarkdownText(text: String, modifier: Modifier = Modifier, streaming: Boolean = false) {
    // Parsing is memoised: while a message streams only the completed prefix is
    // parsed, and it changes at paragraph boundaries instead of per token.
    val split = remember(text, streaming) {
        if (streaming) Markdown.streamingSplit(text) else null
    }
    val complete = split?.first ?: text
    val tail = split?.second.orEmpty()
    val blocks = remember(complete) { Markdown.parse(complete) }
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for (block in blocks) {
            when (block) {
                is MarkdownBlock.Paragraph -> SelectionContainer {
                    InlineText(block.text, MaterialTheme.typography.bodyMedium)
                }
                is MarkdownBlock.Heading -> InlineText(
                    text = block.text,
                    style = when (block.level) {
                        1 -> MaterialTheme.typography.titleLarge
                        2 -> MaterialTheme.typography.titleMedium
                        else -> MaterialTheme.typography.titleSmall
                    },
                    fontWeight = FontWeight.SemiBold,
                )
                is MarkdownBlock.Code -> CodeBlock(block.language, block.code)
                is MarkdownBlock.Bullets -> Column(
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    for (item in block.items) {
                        Row(verticalAlignment = Alignment.Top) {
                            Text("•  ", style = MaterialTheme.typography.bodyMedium)
                            SelectionContainer {
                                InlineText(item, MaterialTheme.typography.bodyMedium)
                            }
                        }
                    }
                }
                is MarkdownBlock.Quote -> Column(
                    Modifier
                        .fillMaxWidth()
                        .background(
                            MaterialTheme.colorScheme.surfaceVariant,
                            RoundedCornerShape(10.dp),
                        )
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                ) {
                    InlineText(
                        text = block.text,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                is MarkdownBlock.Table -> MarkdownTable(block)
                MarkdownBlock.Rule -> Column(
                    Modifier
                        .fillMaxWidth()
                        .padding(vertical = 4.dp)
                        .background(MaterialTheme.colorScheme.outlineVariant)
                        .padding(vertical = 0.5.dp),
                ) {}
            }
        }
        if (tail.isNotBlank()) {
            // The unfinished tail has no stable Markdown structure yet, so it is
            // drawn as plain text and becomes formatted once the turn ends.
            Text(
                text = tail.trimStart('\n'),
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

/**
 * A Text whose inline spans are computed once per string. Rebuilding the
 * AnnotatedString on every recomposition forced Compose to re-layout every
 * paragraph of a streaming answer.
 */
@Composable
private fun InlineText(
    text: String,
    style: TextStyle,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontWeight: FontWeight? = null,
    textAlign: TextAlign? = null,
) {
    val annotated = remember(text) { Markdown.inline(text) }
    Text(
        text = annotated,
        style = style,
        modifier = modifier,
        color = color,
        fontWeight = fontWeight,
        textAlign = textAlign,
    )
}

/**
 * Draws the whole table: every header and every row, with a visible grid.
 * Wide tables scroll horizontally instead of being cut off.
 */
@Composable
private fun MarkdownTable(table: MarkdownBlock.Table) {
    val columns = table.headers.size
    if (columns == 0) return
    // One width per column, sized to its longest cell, so narrow columns stay
    // narrow and documentation tables are not squeezed into equal boxes.
    val widths = remember(table) {
        List(columns) { column ->
            val longest = (listOf(table.headers.getOrElse(column) { "" }) +
                table.rows.map { row -> row.getOrElse(column) { "" } })
                .maxOfOrNull { cell -> cell.length } ?: 0
            (longest.coerceIn(6, 44) * 5.6f + 18f).dp.coerceIn(88.dp, 220.dp)
        }
    }
    val totalWidth = widths.fold(0.dp) { sum, width -> sum + width }
    val border = BorderStroke(0.5.dp, MaterialTheme.colorScheme.outlineVariant)
    androidx.compose.material3.Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceContainerLow,
    ) {
        Column(
            Modifier
                .horizontalScroll(rememberScrollState())
                .width(totalWidth),
        ) {
            Row {
                table.headers.forEachIndexed { index, header ->
                    TableCell(
                        text = header,
                        width = widths.getOrElse(index) { 120.dp },
                        border = border,
                        header = true,
                        align = table.alignments.getOrElse(index) { TextAlign.Start },
                    )
                }
            }
            table.rows.forEachIndexed { rowIndex, row ->
                Row {
                    row.forEachIndexed { index, cell ->
                        TableCell(
                            text = cell,
                            width = widths.getOrElse(index) { 120.dp },
                            border = border,
                            header = false,
                            striped = rowIndex % 2 == 1,
                            align = table.alignments.getOrElse(index) { TextAlign.Start },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun TableCell(
    text: String,
    width: androidx.compose.ui.unit.Dp,
    border: BorderStroke,
    header: Boolean,
    striped: Boolean = false,
    align: TextAlign,
) {
    val color = when {
        header -> MaterialTheme.colorScheme.surfaceContainerHighest
        striped -> MaterialTheme.colorScheme.surfaceContainerLow
        else -> MaterialTheme.colorScheme.surface
    }
    Box(
        Modifier
            .width(width)
            .border(border)
            .background(color)
            .padding(horizontal = 8.dp, vertical = 6.dp),
    ) {
        SelectionContainer {
            InlineText(
                text = text,
                style = if (header) {
                    MaterialTheme.typography.labelLarge
                } else {
                    MaterialTheme.typography.bodySmall
                },
                fontWeight = if (header) FontWeight.SemiBold else null,
                textAlign = align,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

@Composable
private fun CodeBlock(language: String, code: String) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var copied by remember { mutableStateOf(false) }
    Column(
        Modifier
            .fillMaxWidth()
            .background(
                MaterialTheme.colorScheme.surfaceContainerHighest,
                RoundedCornerShape(12.dp),
            ),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(start = 12.dp, end = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = language.ifBlank { "code" },
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            IconButton(
                onClick = {
                    val clipboard = context.getSystemService(
                        android.content.Context.CLIPBOARD_SERVICE,
                    ) as android.content.ClipboardManager
                    clipboard.setPrimaryClip(
                        android.content.ClipData.newPlainText("code", code),
                    )
                    copied = true
                    scope.launch {
                        delay(1500)
                        copied = false
                    }
                },
            ) {
                Icon(
                    if (copied) Icons.Filled.Check else Icons.Filled.ContentCopy,
                    contentDescription = "Copy code",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        SelectionContainer {
            Text(
                text = code,
                modifier = Modifier
                    .horizontalScroll(rememberScrollState())
                    .padding(start = 12.dp, end = 12.dp, bottom = 10.dp),
                style = MaterialTheme.typography.bodySmall.copy(
                    fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurface,
                ),
            )
        }
    }
}
