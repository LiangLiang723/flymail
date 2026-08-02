<script setup lang="ts">
import { computed, ref } from 'vue';

import RemoteImageControl from './RemoteImageControl.vue';
import { sanitizeMailHtml } from './body-sanitizer.ts';

const props = defineProps<{
  html?: string;
  text?: string;
  allowRemoteImages?: boolean;
}>();
const emit = defineEmits<{ openImage: [src: string] }>();
const remoteAllowed = ref(Boolean(props.allowRemoteImages));
const rawHtml = computed(() => props.html || `<pre>${escapeText(props.text || '')}</pre>`);
const sanitized = computed(() => sanitizeMailHtml(rawHtml.value, { allowRemoteImages: remoteAllowed.value }));
const sanitizedHtml = computed(() => sanitized.value.html);

function escapeText(value: string): string {
  return value.replace(/[&<>]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[character] || character);
}

function handleClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null;
  const image = target?.closest('img') as HTMLImageElement | null;
  const src = image?.getAttribute('src');
  if (src) emit('openImage', src);
}
</script>

<template>
  <div class="v2-message-body-shell">
    <RemoteImageControl
      :count="sanitized.blockedRemoteImages.length"
      :allowed="remoteAllowed"
      @change="remoteAllowed = $event"
    />
    <div
      class="v2-mail-body"
      data-mail-body
      @click="handleClick"
      v-html="sanitizedHtml"
    />
  </div>
</template>
