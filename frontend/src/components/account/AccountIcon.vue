<template>
  <span
    class="account-icon-shell"
    :class="`account-icon-shell--${size}`"
    :role="decorative ? undefined : 'img'"
    :aria-label="decorative ? undefined : label"
    :aria-hidden="decorative ? 'true' : undefined"
  >
    <img
      v-if="showUpload"
      class="account-icon-image"
      :src="account.icon_url"
      :alt="decorative ? '' : label"
      @error="uploadFailed = true"
    />
    <span v-else class="account-icon-svg" v-html="resolvedSvg"></span>
  </span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { AccountIconType } from '../../types/account';
import { accountIconPresetSvg, isAccountIconPreset } from '../../utils/account-icon-presets';
import { providerIcon } from '../../utils/provider';

const props = withDefaults(defineProps<{
  account: {
    email: string;
    provider: string;
    remark?: string;
    icon_type?: AccountIconType;
    icon_value?: string;
    icon_url?: string;
  };
  size?: 'sm' | 'md' | 'lg';
  decorative?: boolean;
}>(), {
  size: 'md',
  decorative: false,
});

const uploadFailed = ref(false);
const label = computed(() => `${props.account.remark || props.account.email} 的邮箱图标`);
const showUpload = computed(() => props.account.icon_type === 'upload' && Boolean(props.account.icon_url) && !uploadFailed.value);
const resolvedSvg = computed(() => {
  const presetId = props.account.icon_value || '';
  if (props.account.icon_type === 'preset' && isAccountIconPreset(presetId)) {
    return accountIconPresetSvg(presetId);
  }
  return providerIcon(props.account.provider);
});

watch(
  () => [props.account.icon_type, props.account.icon_value, props.account.icon_url],
  () => { uploadFailed.value = false; },
);
</script>

<style scoped>
.account-icon-shell {
  display: inline-grid;
  flex: none;
  place-items: center;
  overflow: hidden;
  border: 1px solid var(--ui-border);
  border-radius: 9px;
  background: var(--ui-surface-2);
  box-shadow: var(--ui-shadow-xs);
}

.account-icon-shell--sm { width: 20px; height: 20px; border-radius: 6px; }
.account-icon-shell--md { width: 32px; height: 32px; }
.account-icon-shell--lg { width: 48px; height: 48px; border-radius: 13px; }

.account-icon-image,
.account-icon-svg,
.account-icon-svg :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

.account-icon-image { object-fit: cover; }
.account-icon-svg { color: var(--ui-text-2); }
</style>
