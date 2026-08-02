import { createApp, h } from 'vue';

const Placeholder = {
  name: 'FlyMailV2Placeholder',
  render: () => h('main', { class: 'v2-placeholder', 'aria-label': 'FlyMail V2' }, 'FlyMail V2'),
};

createApp(Placeholder).mount('#app');
