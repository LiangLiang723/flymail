<script setup lang="ts">
import { nextTick, ref, watch } from 'vue';

import type { NavigationAccount, SavedSearchNavigationItem } from '../../entities/account/types.ts';
import NavigationPanel from './NavigationPanel.vue';

const props = defineProps<{
  open: boolean;
  accounts: NavigationAccount[];
  savedSearches?: SavedSearchNavigationItem[];
  returnFocus?: HTMLElement | null;
}>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLElement | null>(null);

function closeAfterNavigation(event: MouseEvent) {
  const target = event.target as HTMLElement | null;
  if (target?.closest('a')) emit('close');
}

watch(() => props.open, async (open) => {
  if (open) {
    await nextTick();
    dialog.value?.focus();
  } else {
    props.returnFocus?.focus();
  }
});
</script>

<template>
  <div v-if="open" class="v2-drawer-backdrop" @click.self="emit('close')">
    <aside
      ref="dialog"
      class="v2-mobile-drawer"
      role="dialog"
      aria-modal="true"
      aria-label="邮箱导航菜单"
      tabindex="-1"
      @keydown.esc="emit('close')"
    >
      <button type="button" aria-label="关闭导航" @click="emit('close')">关闭</button>
      <div @click="closeAfterNavigation">
        <NavigationPanel :accounts="accounts" :saved-searches="savedSearches" />
      </div>
    </aside>
  </div>
</template>
