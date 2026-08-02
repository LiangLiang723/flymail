import { createPinia } from 'pinia';
import { createApp } from 'vue';

import AppV2 from './app/AppV2.vue';
import { createV2Router } from './app/router.ts';
import './styles/v2-tokens.css';
import './styles/v2-base.css';
import './styles/v2-layout.css';

const app = createApp(AppV2);
app.use(createPinia());
app.use(createV2Router());
app.mount('#app');
