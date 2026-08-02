import { defineComponent, h } from 'vue';

function page(name: string, title: string) {
  return defineComponent({
    name,
    setup: () => () => h('section', { class: 'v2-placeholder-page', 'aria-label': title }, [
      h('h1', title),
      h('p', '该模块正在使用 FlyMail V2 本地状态。'),
    ]),
  });
}

export const MailPlaceholder = page('MailPlaceholder', '邮件');
export const SearchPlaceholder = page('SearchPlaceholder', '搜索');
export const ComposePlaceholder = page('ComposePlaceholder', '写信');
export const SettingsPlaceholder = page('SettingsPlaceholder', '设置');
export const SyncPlaceholder = page('SyncPlaceholder', '同步中心');
export const AdminPlaceholder = page('AdminPlaceholder', '管理员');
export const BackupPlaceholder = page('BackupPlaceholder', '备份');
export const AboutPlaceholder = page('AboutPlaceholder', '关于');
