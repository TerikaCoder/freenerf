package dev.freenerf.app

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Instrumented smoke test for the primary navigation. Requires a connected
 * device or emulator with an arm64 or x86_64 library build.
 */
@RunWith(AndroidJUnit4::class)
class AppNavigationTest {
    @get:Rule
    val rule = createAndroidComposeRule<MainActivity>()

    @Test
    fun bottomNavigationReachesEveryPrimaryScreen() {
        rule.onNodeWithText("Chat").assertIsDisplayed()
        rule.onNodeWithText("Sessions").performClick()
        rule.onNodeWithText("Sessions").assertIsDisplayed()
        rule.onNodeWithText("Settings").performClick()
        rule.onNodeWithText("Generation").assertIsDisplayed()
    }
}
