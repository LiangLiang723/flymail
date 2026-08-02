<script setup lang="ts">
import { RouterLink } from 'vue-router';

import type { NavigationAccountModel } from '../../entities/account/types.ts';
import { navigationLocation } from './navigation-state.ts';

defineProps<{
  account: NavigationAccountModel;
  expanded: boolean;
}>();
const emit = defineEmits<{
  toggle: [accountId: string];
  action: [accountId: string, action: NonNullable<NavigationAccountModel['action']>];
}>();
</script>

<template>
  <section class="v2-account-section">
    <div class="v2-account-section__header">
      <button
        type="button"
        class="v2-navigation-button"
        :aria-expanded="expanded"
        :aria-controls="`account-${account.id}`"
        @click="emit('toggle', account.id)"
      >
        <span aria-hidden="true">{{ expanded ? '▾' : '▸' }}</span>
        <span>{{ account.displayName }}</span>
        <small>{{ account.email }}</small>
      </button>
      <button
        v-if="account.action"
        type="button"
        class="v2-navigation-action"
        @click="emit('action', account.id, account.action)"
      >
        {{ account.action === 'reauthorize' ? '重新授权' : account.action === 'enable' ? '启用' : '验证' }}
      </button>
    </div>
    <ul v-if="expanded" :id="`account-${account.id}`" class="v2-navigation-list">
      <li v-for="mailbox in account.semanticMailboxes" :key="`mailbox-${mailbox.key}`">
        <RouterLink :to="navigationLocation({ kind: 'account', accountId: account.id, key: mailbox.key })">
          <span>{{ mailbox.name }}</span><span v-if="mailbox.unreadCount">{{ mailbox.unreadCount }}</span>
        </RouterLink>
      </li>
      <li v-for="label in account.nativeLabels" :key="`label-${label.key}`">
        <RouterLink :to="navigationLocation({ kind: 'native', accountId: account.id, key: label.key, semanticKey: label.semanticKey })">
          <span>{{ label.name }}</span><span v-if="label.unreadCount">{{ label.unreadCount }}</span>
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
