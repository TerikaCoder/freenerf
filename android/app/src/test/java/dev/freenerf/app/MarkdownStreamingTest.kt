package dev.freenerf.app

import dev.freenerf.app.ui.components.Markdown
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownStreamingTest {
    @Test
    fun splitsAtTheLastBlankLine() {
        val (prefix, tail) = Markdown.streamingSplit("Para one\n\nPara two")
        assertEquals("Para one", prefix)
        assertEquals("\n\nPara two", tail)
    }

    @Test
    fun neverSplitsInsideAnOpenCodeFence() {
        val text = "Text\n\n```kotlin\nval a = 1\n\nval b = 2"
        val (prefix, tail) = Markdown.streamingSplit(text)
        assertEquals("Text", prefix)
        assertEquals("\n\n```kotlin\nval a = 1\n\nval b = 2", tail)
        assertTrue(Markdown.parse(prefix).none { it is dev.freenerf.app.ui.components.MarkdownBlock.Code })
    }

    @Test
    fun keepsEverythingAsTailWithoutABoundary() {
        val (prefix, tail) = Markdown.streamingSplit("one **bold** line")
        assertEquals("", prefix)
        assertEquals("one **bold** line", tail)
    }

    @Test
    fun splitsAClosedFenceNormally() {
        val text = "Intro\n\n```sh\nls\n```\n\nDone"
        val (prefix, tail) = Markdown.streamingSplit(text)
        assertEquals("Intro\n\n```sh\nls\n```", prefix)
        assertEquals("\n\nDone", tail)
    }
}
