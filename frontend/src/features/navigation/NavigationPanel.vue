<script setup lang="ts">
import { computed, reactive } from 'vue';
import { RouterLink } from 'vue-router';

import type { NavigationAccount, SavedSearchNavigationItem } from '../../entities/account/types.ts';
import AccountSection from './AccountSection.vue';
import { buildNavigationModel, createNavigationState, navigationLocation } from './navigation-state.ts';

const props = defineProps<{
  accounts: NavigationAccount[];
  savedSearches?: SavedSearchNavigationItem[];
  expandedAccountIds?: string[];
}>();
const emit = defineEmits<{
  preference: [value: { expanded_account_ids: string[] }];
  accountAction: [accountId: string, action: 'reauthorize' | 'enable' | 'verify'];
}>();
const navigation = reactive(createNavigationState({ expandedAccountIds: props.expandedAccountIds }));
const model = computed(() => buildNavigationModel(props.accounts, props.savedSearches || []));

function toggleAccount(accountId: string) {
  navigation.toggleAccount(accountId);
  emit('preference', navigation.preference());
}

function accountAction(accountId: string, action: 'reauthorize' | 'enable' | 'verify') {
  emit('accountAction', accountId, action);
}
</script>

<template>
  <nav class="v2-navigation-panel" aria-label="邮箱导航">
    <div class="v2-navigation-brand">
      <strong>FlyMail</strong>
      <RouterLink to="/compose" class="v2-compose-link">写信</RouterLink>
    </div>

    <section aria-labelledby="unified-folders-title">
      <h2 id="unified-folders-title">统一文件夹</h2>
      <ul class="v2-navigation-list">
        <li v-for="item in model.semantic" :key="item.key">
          <RouterLink :to="navigationLocation({ kind: 'semantic', key: item.key })">
            <span>{{ item.name }}</span><span v-if="item.unreadCount">{{ item.unreadCount }}</span>
          </RouterLink>
        </li>
      </ul>
    </section>

    <section v-if="model.savedSearches.length" aria-labelledby="saved-searches-title">
      <h2 id="saved-searches-title">保存的搜索</h2>
      <ul class="v2-navigation-list">
        <li v-for="item in model.savedSearches" :key="item.id">
          <RouterLink :to="navigationLocation({ kind: 'saved', id: item.id })">{{ item.name }}</RouterLink>
        </li>
      </ul>
    </section>

    <section aria-labelledby="accounts-title">
      <h2 id="accounts-title">邮箱账号</h2>
      <AccountSection
        v-for="account in model.accounts"
        :key="account.id"
        :account="account"
        :expanded="navigation.isExpanded(account.id)"
        @toggle="toggleAccount"
        @action="accountAction"
      />
    </section>
  </nav>
</template>
