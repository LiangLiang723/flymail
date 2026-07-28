/** WebSocket 连接管理：模块级单例、引用计数与多播监听。 */
import { onUnmounted, ref, type Ref } from 'vue';

export interface WebSocketMessage {
  type: string;
  [key: string]: any;
}

type MessageListener = (msg: WebSocketMessage) => void;

let sharedSocket: WebSocket | null = null;
const sharedConnected = ref(false);
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 3000;
const MAX_RECONNECT_DELAY = 60000;
let refCount = 0;
const listeners = new Set<MessageListener>();

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function buildWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const basePath = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
  return `${protocol}//${window.location.host}${basePath || ''}/ws`;
}

function openSharedSocket() {
  if (sharedSocket || refCount <= 0) return;
  try {
    const socket = new WebSocket(buildWsUrl());
    socket.onopen = () => {
      sharedConnected.value = true;
      reconnectDelay = 3000;
    };
    socket.onmessage = (event) => {
      try {
        const data: WebSocketMessage = JSON.parse(event.data);
        if (data.type === 'ping') {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'pong' }));
          }
          return;
        }
        listeners.forEach((listener) => {
          try {
            listener(data);
          } catch {
            // 单个订阅者异常不影响其他页面。
          }
        });
      } catch {
        // 忽略非 JSON 消息。
      }
    };
    socket.onclose = () => {
      sharedConnected.value = false;
      sharedSocket = null;
      if (refCount > 0) {
        clearReconnectTimer();
        reconnectTimer = setTimeout(openSharedSocket, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
      }
    };
    socket.onerror = () => {
      sharedConnected.value = false;
    };
    sharedSocket = socket;
  } catch {
    sharedSocket = null;
    sharedConnected.value = false;
    if (refCount > 0) {
      clearReconnectTimer();
      reconnectTimer = setTimeout(openSharedSocket, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    }
  }
}

function releaseShared() {
  refCount = Math.max(0, refCount - 1);
  if (refCount > 0) return;
  clearReconnectTimer();
  if (sharedSocket) {
    sharedSocket.onclose = null;
    sharedSocket.close();
    sharedSocket = null;
  }
  sharedConnected.value = false;
  reconnectDelay = 3000;
}

export function useWebSocket(onMessage: MessageListener) {
  const ws = ref<WebSocket | null>(null) as Ref<WebSocket | null>;
  const wsConnected = sharedConnected;
  let acquired = false;

  function connect() {
    if (acquired) return;
    acquired = true;
    listeners.add(onMessage);
    refCount += 1;
    openSharedSocket();
    ws.value = sharedSocket;
  }

  function disconnect() {
    if (!acquired) return;
    acquired = false;
    listeners.delete(onMessage);
    releaseShared();
    ws.value = sharedSocket;
  }

  onUnmounted(disconnect);

  return { ws, wsConnected, connect, disconnect };
}
