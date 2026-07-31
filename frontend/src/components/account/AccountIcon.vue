<template>
  <span
    class="account-icon-shell"
    :class="{
      'account-icon-shell--upload': iconKind === 'upload',
      'account-icon-shell--preset': iconKind === 'preset',
      'account-icon-shell--provider': iconKind === 'provider',
    }"
    :style="shellStyle"
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
  size?: 16 | 18 | 24 | 30 | 36 | 48;
  decorative?: boolean;
}>(), {
  size: 30,
  decorative: false,
});

const uploadFailed = ref(false);
const label = computed(() => `${props.account.remark || props.account.email} 的邮箱图标`);
const showUpload = computed(() => props.account.icon_type === 'upload' && Boolean(props.account.icon_url) && !uploadFailed.value);
const hasPreset = computed(() => props.account.icon_type === 'preset' && isAccountIconPreset(props.account.icon_value || ''));
const iconKind = computed<'upload' | 'preset' | 'provider'>(() => {
  if (showUpload.value) return 'upload';
  if (hasPreset.value) return 'preset';
  return 'provider';
});
const shellStyle = computed(() => ({
  '--account-icon-size': `${props.size}px`,
  '--account-icon-radius': `${Math.max(4, Math.round(props.size * 0.28))}px`,
}) as Record<string, string>);
const resolvedSvg = computed(() => {
  const presetId = props.account.icon_value || '';
  if (hasPreset.value) return accountIconPresetSvg(presetId);
  return providerIcon(props.account.provider);
});

watch(
  () => [props.account.icon_type, props.account.icon_value, props.account.icon_url],
  () => { uploadFailed.value = false; },
);
</script>

<style scoped>
.account-icon-shell {
  width: var(--account-icon-size);
  height: var(--account-icon-size);
  display: inline-grid;
  flex: none;
  place-items: center;
  overflow: hidden;
  border-radius: var(--account-icon-radius);
  background: transparent;
  line-height: 0;
}

.account-icon-image,
.account-icon-shell--preset .account-icon-svg,
.account-icon-shell--preset .account-icon-svg :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

.account-icon-shell--provider .account-icon-svg,
.account-icon-shell--provider .account-icon-svg :deep(svg) {
  display: block;
  width: 16px;
  height: 16px;
}

.account-icon-image { object-fit: cover; }
.account-icon-svg { color: var(--ui-text-2); }
</style>
