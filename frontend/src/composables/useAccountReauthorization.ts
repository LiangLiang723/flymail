import { useMailStore } from '../stores/mail';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';
import {
  authWindowBlockedMessage,
  closeAuthWindow,
  navigateAuthWindow,
  openAuthWindowSync,
} from '../utils/oauthWindow';

export function useAccountReauthorization() {
  const mailStore = useMailStore();
  const uiStore = useUIStore();

  async function reauthorizeAccount(accountId?: string): Promise<void> {
    const targetId = accountId || mailStore.currentAccountId;
    const targetAccount = mailStore.accounts.find((account) => account.id === targetId);
    if (!targetAccount) return;

    const provider = targetAccount.provider;
    const providerLabel = provider === 'outlook' ? 'Microsoft' : 'Google';
    const { win: authWindow } = openAuthWindowSync(providerLabel);
    if (!authWindow) {
      uiStore.error(authWindowBlockedMessage(providerLabel));
      return;
    }

    try {
      const settingsData = await api.get('/settings') as any;
      const settings = settingsData.settings || {};
      const redirectUri = provider === 'outlook'
        ? settings.outlook_redirect_uri || ''
        : settings.gmail_redirect_uri || '';

      if (!redirectUri) {
        closeAuthWindow(authWindow);
        uiStore.error(provider === 'outlook'
          ? '请先在设置页面配置 Microsoft 重定向 URI'
          : '请先在设置页面配置 Gmail 重定向 URI');
        return;
      }

      sessionStorage.setItem('flymail_oauth_reauth', '1');
      const data = await api.post('/accounts/auth-url', { provider, redirect_uri: redirectUri }) as any;
      if (data.error) {
        closeAuthWindow(authWindow);
        uiStore.error('获取授权链接失败：' + data.error);
        return;
      }
      if (!data.auth_url) {
        closeAuthWindow(authWindow);
        uiStore.error('获取授权链接失败');
        return;
      }
      if (!navigateAuthWindow(authWindow, data.auth_url)) {
        uiStore.error(authWindowBlockedMessage(providerLabel));
      }
    } catch (error: any) {
      closeAuthWindow(authWindow);
      uiStore.error('重新授权失败：' + (error.response?.data?.error || error.message || '网络错误'));
    }
  }

  return { reauthorizeAccount };
}
