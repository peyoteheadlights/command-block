package io.github.peyoteheadlights.mcsc.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TopViewTest {
    @Test
    fun `the open tab is in front, then the ones after it, then around`() {
        val strip = listOf(null, "a", "b", "c")
        assertEquals(listOf("b", "c", null, "a"), TopView.order(strip, "b"))
        assertEquals(strip, TopView.order(strip, null))
        assertEquals(listOf("c", null, "a", "b"), TopView.order(strip, "c"))
    }

    @Test
    fun `a server that is gone leaves the strip as it is`() {
        assertEquals(listOf(null, "a"), TopView.order(listOf(null, "a"), "removed"))
    }

    @Test
    fun `a pull opens it only when it went far enough`() {
        assertFalse(TopView.opens(wasOpen = false, pulled = 40f, distance = 300f))
        assertTrue(TopView.opens(wasOpen = false, pulled = 90f, distance = 300f))
        assertFalse(TopView.opens(wasOpen = false, pulled = -200f, distance = 300f))
    }

    @Test
    fun `an open one closes only when pushed back far enough`() {
        assertTrue(TopView.opens(wasOpen = true, pulled = -40f, distance = 300f))
        assertFalse(TopView.opens(wasOpen = true, pulled = -90f, distance = 300f))
        assertTrue(TopView.opens(wasOpen = true, pulled = 120f, distance = 300f))
    }
}
