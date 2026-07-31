const mailSvg = (color: string) => `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="4" fill="${color}"/><path d="m4.5 7 7.5 6 7.5-6" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const iconSvg = (path: string, background: string) => `<svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="${background}"/><path d="${path}" fill="none" stroke="white" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const WORK_SVG = iconSvg('M7 8V6.5A2.5 2.5 0 0 1 9.5 4h5A2.5 2.5 0 0 1 17 6.5V8M4 9h16v10H4zM4 12.5c4.8 2.2 11.2 2.2 16 0M10 13h4', '#5e5ce6');
const PERSONAL_SVG = iconSvg('M12 12a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm-6 7c.7-3.2 2.7-5 6-5s5.3 1.8 6 5', '#af52de');
const SCHOOL_SVG = iconSvg('m3.5 9 8.5-4 8.5 4-8.5 4-8.5-4Zm3 2.2V16c3 2.2 8 2.2 11 0v-4.8M20.5 9v6', '#007aff');
const TEAM_SVG = iconSvg('M9.5 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7-1a2.5 2.5 0 1 0 0-5M4 19c.5-3.6 2.3-5.5 5.5-5.5S14.5 15.4 15 19m.5-6c2.8 0 4.3 1.7 4.5 5', '#34c759');
const STAR_SVG = iconSvg('m12 4 2.3 4.7 5.2.8-3.8 3.7.9 5.3-4.6-2.5-4.6 2.5.9-5.3-3.8-3.7 5.2-.8L12 4Z', '#ff9f0a');

export const ACCOUNT_ICON_PRESETS = [
  { id: 'mail-purple', label: '紫色邮件', svg: mailSvg('#7c79ff') },
  { id: 'mail-blue', label: '蓝色邮件', svg: mailSvg('#0a84ff') },
  { id: 'mail-green', label: '绿色邮件', svg: mailSvg('#30d158') },
  { id: 'work', label: '工作', svg: WORK_SVG },
  { id: 'personal', label: '个人', svg: PERSONAL_SVG },
  { id: 'school', label: '学校', svg: SCHOOL_SVG },
  { id: 'team', label: '团队', svg: TEAM_SVG },
  { id: 'star', label: '星标', svg: STAR_SVG },
] as const;

export type AccountIconPresetId = typeof ACCOUNT_ICON_PRESETS[number]['id'];

export function isAccountIconPreset(id: string): id is AccountIconPresetId {
  return ACCOUNT_ICON_PRESETS.some((preset) => preset.id === id);
}

export function accountIconPresetSvg(id: string): string {
  return ACCOUNT_ICON_PRESETS.find((preset) => preset.id === id)?.svg || '';
}
