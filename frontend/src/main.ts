import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles/tokens.css'
import './styles/base.css'
import './styles/macos.css'
import './styles/components.css'
import './styles/app-shell.css'
import './styles/layout-system.css'
import './styles/page-system.css'
import { themeController } from './utils/theme'

themeController.initialize()

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
