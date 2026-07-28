import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles/macos.css'
import { themeController } from './utils/theme'

themeController.initialize()

const app = createApp(App)
app.use(createPinia())
app.mount('#app')
