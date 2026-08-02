export async function registerFlyMailServiceWorker(): Promise<ServiceWorkerRegistration | undefined> {
  if (!import.meta.env.PROD) return undefined;
  if (!('serviceWorker' in navigator)) return undefined;
  return navigator.serviceWorker.register('/flymail-sw.js', { scope: '/' });
}
