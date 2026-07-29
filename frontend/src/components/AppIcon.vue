<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <g v-if="name === 'inbox'">
      <path d="M4 5h16l1 10H3L4 5Z" />
      <path d="M3 15h5l2 3h4l2-3h5" />
    </g>
    <g v-else-if="name === 'mail'">
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m4 7 8 6 8-6" />
    </g>
    <g v-else-if="name === 'contacts' || name === 'users'">
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19c.5-4 2.5-6 5.5-6s5 2 5.5 6" />
      <path v-if="name === 'users'" d="M15 6.5a3 3 0 0 1 0 5.5M16.5 13.5c2.2.7 3.5 2.5 4 5.5" />
    </g>
    <g v-else-if="name === 'sync'">
      <path d="M20 7h-5V2" />
      <path d="M4 17h5v5" />
      <path d="M5.5 9A7 7 0 0 1 17.8 5L20 7" />
      <path d="M18.5 15A7 7 0 0 1 6.2 19L4 17" />
    </g>
    <g v-else-if="name === 'accounts'">
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c.6-4.4 3-6.5 7-6.5s6.4 2.1 7 6.5" />
    </g>
    <g v-else-if="name === 'backup' || name === 'archive'">
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M3 5h18M9 10h6" />
    </g>
    <g v-else-if="name === 'settings'">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9A1.7 1.7 0 0 0 21 10h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </g>
    <g v-else-if="name === 'bell' || name === 'notifications'">
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
      <path d="M10 21h4" />
    </g>
    <g v-else-if="name === 'info'">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </g>
    <g v-else-if="name === 'chevron-down'">
      <path d="m7 10 5 5 5-5" />
    </g>
    <g v-else-if="name === 'lock'">
      <rect x="5" y="10" width="14" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </g>
    <g v-else-if="name === 'logout'">
      <path d="M10 5H5v14h5" />
      <path d="m14 8 4 4-4 4M18 12H9" />
    </g>
    <g v-else-if="name === 'send'">
      <path d="m22 2-7 20-4-9-9-4 20-7Z" />
      <path d="M22 2 11 13" />
    </g>
    <g v-else-if="name === 'draft'">
      <path d="M5 3h10l4 4v14H5V3Z" />
      <path d="M14 3v5h5M8 13h8M8 17h6" />
    </g>
    <g v-else-if="name === 'junk'">
      <path d="M4 5h16l-1 16H5L4 5Z" />
      <path d="M9 10h.01M15 10h.01M9 16c1.6-1.3 4.4-1.3 6 0" />
      <path d="M3 5h18M9 2h6" />
    </g>
    <g v-else-if="name === 'trash'">
      <path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14" />
    </g>
    <g v-else-if="name === 'star'">
      <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9L12 3Z" />
    </g>
    <g v-else>
      <path d="M3 7h7l2 2h9v11H3V7Z" />
      <path d="M3 7V5h7l2 2" />
    </g>
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  name: string;
  size?: number;
}>(), {
  size: 20,
});
</script>
