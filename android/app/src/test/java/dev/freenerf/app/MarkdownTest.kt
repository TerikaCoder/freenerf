package dev.freenerf.app

import dev.freenerf.app.ui.components.Markdown
import dev.freenerf.app.ui.components.MarkdownBlock
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTest {
    @Test
    fun parsesFencedCodeBlocks() {
        val blocks = Markdown.parse("Before\n\n```kotlin\nval x = 1\n```\nAfter")
        assertEquals(3, blocks.size)
        assertTrue(blocks[0] is MarkdownBlock.Paragraph)
        val code = blocks[1] as MarkdownBlock.Code
        assertEquals("kotlin", code.language)
        assertEquals("val x = 1", code.code)
        assertEquals("After", (blocks[2] as MarkdownBlock.Paragraph).text)
    }

    @Test
    fun keepsUnclosedFenceAsCodeWhileStreaming() {
        val blocks = Markdown.parse("Here:\n```\nval x = 1")
        val code = blocks.last() as MarkdownBlock.Code
        assertEquals("val x = 1", code.code)
    }

    @Test
    fun parsesHeadingsBulletsAndQuotes() {
        val blocks = Markdown.parse("# Title\n\n- one\n- two\n\n> quoted")
        assertTrue(blocks[0] is MarkdownBlock.Heading)
        assertEquals(2, (blocks[1] as MarkdownBlock.Bullets).items.size)
        assertEquals("quoted", (blocks[2] as MarkdownBlock.Quote).text)
    }

    @Test
    fun inlineFormattingProducesSpans() {
        val text = Markdown.inline("Use `code`, **bold** and *italic*.")
        assertEquals("Use code, bold and italic.", text.text)
        assertTrue(text.spanStyles.isNotEmpty())
    }
}
