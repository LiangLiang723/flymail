import { createPinia } from 'pinia';
import { createApp } from 'vue';

import AppV2 from './app/AppV2.vue';
import { createV2Router } from './app/router.ts';
import { registerFlyMailServiceWorker } from './features/pwa/register.ts';
import './styles/tokens.css';
import './styles/base.css';
import './styles/components.css';
import './styles/app-shell.css';
import './styles/layout-system.css';
import './styles/page-system.css';
import './styles/macos.css';
import './styles/v2-tokens.css';
import './styles/v2-base.css';
import './styles/v2-layout.css';
import './styles/v1-v2-compat.css';

const app = createApp(AppV2);
app.use(createPinia());
app.use(createV2Router());
app.mount('#app');
void registerFlyMailServiceWorker().catch(() => undefined);
