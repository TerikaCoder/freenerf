package dev.freenerf.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.freenerf.app.ui.RootScreen
import dev.freenerf.app.ui.theme.FreeNerfTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val container = (application as FreeNerfApp).container
        setContent {
            val settings by container.settings.collectAsStateWithLifecycle()
            FreeNerfTheme(settings = settings) {
                Surface(Modifier.fillMaxSize()) {
                    RootScreen(container)
                }
            }
        }
    }
}
