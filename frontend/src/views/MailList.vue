<template>
  <PageFrame template="workspace" width="fluid" class="mail-view ui-page">
    <!-- 单账号重新授权提示 -->
    <div v-if="mailStore.accounts.length === 1 && mailStore.reauthAccountIds.has(mailStore.currentAccountId)" class="reauth-banner">
      <span>账号授权已过期</span>
      <button class="btn btn-primary btn-sm" @click="reauthorize(mailStore.currentAccountId)">重新授权</button>
    </div>

    <div class="mail-shell workspace-grid" :class="{ detail: !!selectedMessage }">
    <aside v-if="!isMobile" class="folder-sidebar">
      <header class="folder-sidebar-header">
        <span>文件夹</span>
        <span>{{ mailStore.accounts.length }} 个账号</span>
      </header>

      <div class="account-switcher">
        <div v-for="acc in mailStore.accounts" :key="acc.id" class="account-switcher-row">
          <button
            class="account-switcher-item"
            :class="{ active: mailStore.currentAccountId === acc.id }"
            @click="switchAccount(acc.id)"
          >
            <AccountIcon :account="acc" :size="18" decorative />
            <span class="account-switcher-copy">
              <strong>{{ accountDisplayName(acc) }}</strong>
              <small>{{ acc.email }}</small>
            </span>
          </button>
          <button
            v-if="mailStore.reauthAccountIds.has(acc.id)"
            class="btn-reauth account-reauth"
            @click.stop="reauthorize(acc.id)"
            title="重新授权"
          >
            <AppIcon name="sync" :size="14" />
          </button>
        </div>
      </div>

      <div class="folder-nav-list">
        <button
          v-for="folder in mailStore.folders"
          :key="folder.path"
          class="folder-nav-item"
          :class="{ active: mailStore.currentFolder === folder.path }"
          @click="mailStore.setFolder(folder.path)"
        >
          <AppIcon :name="folderIconName(folder.name)" :size="17" />
          <span class="folder-nav-name">{{ mailStore.folderDisplayName(folder.name) }}</span>
          <span class="folder-nav-count">{{ getFolderCount(folder) }}</span>
        </button>
      </div>
    </aside>

    <!-- 邮件列表视图 -->
    <div v-show="!isMobile || !selectedMessage" class="mail-list">
      <!-- 普通模式工具栏 -->
      <div v-if="!selectMode" class="list-toolbar">
        <div class="toolbar-left">
          <button class="compose-entry-btn" @click="openCompose" title="写邮件">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 5v14"/><path d="M5 12h14"/>
            </svg>
            <span>写邮件</span>
          </button>
          <!-- 多选图标按钮 -->
          <button class="btn-icon" @click="enterSelectMode()" title="多选">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
            </svg>
          </button>
          <!-- 移动端：打开统一导航抽屉 -->
          <button v-if="isMobile" class="folder-picker" @click="openMobileSidebar">
            <span class="picker-label">{{ mailStore.currentFolderName }}</span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          <!-- 桌面端：文件夹名+数量 -->
          <span v-else class="list-count">
            {{ mailStore.currentFolderName }} · {{ noReadStateFolder ? `全部 ${filterCounts.all}` : `未读 ${currentFolderUnreadCount}` }}
          </span>
          <!-- 筛选按钮 -->
          <span class="toolbar-divider"></span>
          <button class="filter-btn" :class="{ active: readFilter === '' && !attachmentFilter }" @click="setReadFilter('')">全部 {{ filterCounts.all }}</button>
          <button v-if="!noReadStateFolder" class="filter-btn" :class="{ active: readFilter === 'unread' }" @click="setReadFilter('unread')">未读 {{ filterCounts.unread }}</button>
          <button v-if="!noReadStateFolder" class="filter-btn" :class="{ active: readFilter === 'read' }" @click="setReadFilter('read')">已读 {{ filterCounts.read }}</button>
          <button class="filter-btn" :class="{ active: attachmentFilter }" @click="setAttachmentFilter()">附件 {{ filterCounts.attachments }}</button>
        </div>
        <div class="toolbar-right">
          <input v-model.trim="searchKeyword" class="search-input" type="search" placeholder="搜索主题/发件人/正文" @keydown.enter="applySearch" />
          <button class="btn-icon" @click="applySearch" title="搜索">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
          </button>
          <button v-if="searchKeyword" class="btn-icon" @click="clearSearch" title="清空搜索">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
          <button v-if="!noReadStateFolder && currentFolderUnreadCount > 0" class="btn-icon" @click="markCurrentFolderAllRead" title="当前文件夹全部已读">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          </button>
          <!-- 移动端：筛选展开/收起按钮 -->
          <button class="btn-icon mobile-filter-toggle" :class="{ active: hasActiveFilter }" type="button" title="筛选邮件" aria-label="筛选邮件" @click="showMobileFilters = !showMobileFilters">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
          </button>
          <UiIconButton
            class="refresh-button"
            :class="{ 'is-refreshing': refreshingLatest }"
            :label="refreshingLatest ? '正在刷新当前文件夹最新邮件' : '刷新当前文件夹最新邮件'"
            :loading="refreshingLatest"
            :disabled="syncing || rebuilding || !mailStore.currentAccountId"
            @click="refreshLatestPage"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.13-3.36L23 10"/><path d="M20.49 15A9 9 0 0 1 6.36 18.36L1 14"/>
            </svg>
          </UiIconButton>
          <UiBadge v-if="rebuilding" tone="accent">{{ syncProgress || '同步中' }}</UiBadge>
          <span class="sync-status" :class="{ connected: wsConnected }" :title="wsConnected ? '实时同步已连接' : '实时同步未连接'">
            <span class="status-dot"></span>
          </span>
        </div>
      <!-- 移动端：筛选下拉菜单（右侧紧凑面板） -->
      <transition name="filter-dropdown">
        <div v-if="showMobileFilters" class="mobile-filter-dropdown">
          <div class="filter-backdrop" @click="showMobileFilters = false"></div>
          <div class="filter-dropdown-menu filter-dropdown-compact">
            <button class="filter-dropdown-item" :class="{ active: readFilter === '' && !attachmentFilter }" @click="setReadFilter(''); showMobileFilters = false">全部 {{ filterCounts.all }}</button>
            <button v-if="!noReadStateFolder" class="filter-dropdown-item" :class="{ active: readFilter === 'unread' }" @click="setReadFilter('unread'); showMobileFilters = false">未读 {{ filterCounts.unread }}</button>
            <button v-if="!noReadStateFolder" class="filter-dropdown-item" :class="{ active: readFilter === 'read' }" @click="setReadFilter('read'); showMobileFilters = false">已读 {{ filterCounts.read }}</button>
            <button class="filter-dropdown-item" :class="{ active: attachmentFilter }" @click="setAttachmentFilter(); showMobileFilters = false">附件 {{ filterCounts.attachments }}</button>
          </div>
        </div>
      </transition>
      </div>

      <!-- 多选模式工具栏（用 template v-else 包裹，保持与 v-if 相邻） -->
      <template v-else>
        <div class="select-toolbar">
        <button class="select-btn" @click="exitSelectMode">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
        <span class="select-info">已选 {{ selectedIds.size }} 封</span>
        <div class="select-actions">
          <button class="select-btn" @click="toggleSelectAll" :title="isAllSelected ? '取消全选' : '全选'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
            </svg>
          </button>
          <button v-if="!noReadStateFolder" class="select-btn mark-read" @click="batchMarkRead" :disabled="selectedIds.size === 0" title="标记已读">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/>
            </svg>
          </button>
          <button class="select-btn delete" @click="batchDelete" :disabled="selectedIds.size === 0" title="删除选中">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
        </div>
      </template>

      <!-- iOS风格底部弹出文件夹选择 -->
      <transition name="sheet">
        <div v-if="showFolderSheet" class="sheet-backdrop" @click.self="showFolderSheet = false">
          <div class="sheet-content">
            <div class="sheet-handle"></div>
            <div class="sheet-title">文件夹</div>
            <div class="sheet-list">
              <button
                v-for="folder in mailStore.folders"
                :key="folder.path"
                class="sheet-item"
                :class="{ active: mailStore.currentFolder === folder.path }"
                @click="mailStore.setFolder(folder.path); showFolderSheet = false"
              >
                <span class="sheet-folder-name">{{ mailStore.folderDisplayName(folder.name) }}</span>
                <span class="sheet-folder-count" v-if="getFolderCount(folder)">{{ getFolderCount(folder) }}</span>
                <svg v-if="mailStore.currentFolder === folder.path" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent-blue)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              </button>
            </div>
          </div>
        </div>
      </transition>

      <!-- 加载中（首次加载无缓存数据时显示） -->
      <UiLoadingState
        v-if="loading && messages.length === 0"
        label="正在加载邮件…"
      />

      <!-- 空状态 -->
      <UiEmptyState
        v-else-if="!loading && messages.length === 0"
        :title="mailStore.currentAccountId ? '暂无邮件' : '暂无邮箱账号'"
        :description="mailStore.currentAccountId ? '当前文件夹没有匹配邮件。' : '请先在账号管理中添加邮箱。'"
      />

      <!-- 邮件列表 -->
      <div v-else class="list-items">
        <button
          v-for="msg in messages"
          :key="msg.id"
          class="mail-item"
          :class="{ unread: !noReadStateFolder && !msg.is_read, selected: selectMode && selectedIds.has(msg.id) }"
          @click="selectMode ? toggleSelect(msg.id) : selectMessage(msg)"
          @mouseenter="prefetchMessage(msg)"
          @contextmenu.prevent="enterSelectMode(msg.id)"
        >
          <!-- 多选模式下的勾选框 -->
          <div v-if="selectMode" class="check-circle" :class="{ checked: selectedIds.has(msg.id) }">
            <svg v-if="selectedIds.has(msg.id)" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <!-- 左列：头像 + 发件人 -->
          <div class="mail-sender">
            <div class="mail-avatar" :style="{ background: getAvatarColor(msg.from_addr) }">
              {{ getInitial(msg.from_addr) }}
            </div>
            <span class="mail-from">{{ extractName(msg.from_addr) }}</span>
          </div>
          <!-- 中列：状态图标 + 主题 + 附件 + 日期 -->
          <div class="mail-info">
            <div class="mail-main-row">
              <!-- 中列：已读/未读图标 + 主题 + 附件 -->
              <svg v-if="!noReadStateFolder && !msg.is_read" class="mail-status-icon unread-icon" width="16" height="16" viewBox="0 0 24 24"><path fill="currentColor" d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 4l-8 5-8-5V6l8 5 8-5v2z"/></svg>
              <svg v-else-if="!noReadStateFolder" class="mail-status-icon read-icon" width="16" height="16" viewBox="0 0 1024 1024" fill="currentColor"><path d="M461.816 79.279c30.333-20.364 69.97-20.373 100.311-0.021l384.19 257.69c9.256 6.208 13.947 16.672 13.216 27.044 0.108 1.548 0.096 3.1-0.034 4.64 0.33 1.778 0.501 3.61 0.501 5.483v495.903C960 919.714 919.706 960 870 960H154c-49.706 0-90-40.286-90-89.982V374.115c0-2.663 0.347-5.245 0.999-7.704-0.004-0.803 0.025-1.608 0.086-2.412-0.804-10.432 3.883-20.985 13.191-27.234z m70.259 519.057c-11.417-10.283-28.76-10.278-40.171 0.012L157.358 900.01h709.674zM124 425.237v424.071L381.796 616.85 124 425.237z m776 0.224L642.268 616.842 900 848.964V425.461zM528.7 129.074a30.005 30.005 0 0 0-33.437 0.007L143.678 365.114l283.558 210.762 24.483-22.075c33.891-30.56 85.223-30.88 119.48-0.952l1.034 0.916 24.56 22.121 283.833-210.763z"/></svg>
              <span class="mail-subject">{{ msg.subject || '(无主题)' }}</span>
              <!-- 附件图标 -->
              <svg v-if="msg.has_attachments" class="att-badge" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            </div>
          </div>
          <!-- 右列：日期（独立固定宽度列，保证最右侧对齐） -->
          <span class="mail-date">{{ formatDate(msg.date) }}</span>
        </button>
      </div>

      <div v-if="!selectMode && mailStore.currentAccountId" class="pagination" :class="{ mobile: isMobile }">
        <button class="page-btn page-nav" :disabled="currentPage <= 1" @click="goPage(currentPage - 1)">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
          <span v-if="isMobile">上一页</span>
        </button>
        <template v-if="isMobile">
          <div class="page-mobile-summary">
            <span class="page-mobile-current">{{ currentPage }}</span>
            <span class="page-mobile-divider">/</span>
            <span class="page-mobile-total">{{ totalPages }}</span>
          </div>
        </template>
        <template v-else>
          <template v-for="p in pageNumbers" :key="p">
            <span v-if="p === '...'" class="page-ellipsis">...</span>
            <button v-else class="page-btn" :class="{ active: p === currentPage }" @click="goPage(p as number)">{{ p }}</button>
          </template>
        </template>
        <button class="page-btn page-nav" :disabled="currentPage >= totalPages" @click="goPage(currentPage + 1)">
          <span v-if="isMobile">下一页</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </div>
    </div>

    <!-- 邮件详情视图（支持左滑返回） -->
    <div v-if="selectedMessage" class="mail-detail"
         @touchstart="onDetailTouchStart"
         @touchmove="onDetailTouchMove"
         @touchend="onDetailTouchEnd">
      <div class="detail-toolbar">
        <button class="btn-back" @click="backToList">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
          <span>返回</span>
        </button>
        <div class="detail-actions">
          <button class="btn-action" @click="replyMessage" title="回复邮件">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>
            </svg>
            <span>回复</span>
          </button>
          <button class="btn-action" @click="forwardMessage" title="转发邮件">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 17 20 12 15 7"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/>
            </svg>
            <span>转发</span>
          </button>
          <button class="btn-action" @click="addSenderToContacts" title="添加发件人为联系人">
            <span>联系人</span>
          </button>
          <button class="btn-action" @click="exportSelectedMessage" title="打印或保存为 PDF">
            <span>导出 PDF</span>
          </button>
          <button class="btn-action" :class="{ confirm: deleteConfirm }" @click="onDeleteMessage" :title="deleteConfirm ? '再次点击确认删除' : '删除邮件'">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
            <span v-if="deleteConfirm">确认删除</span>
          </button>
        </div>
      </div>

      <!-- 标题+正文+附件全部在一个滚动区域内 -->
      <div class="detail-body">
        <div class="detail-header">
          <h2 class="detail-subject">{{ selectedMessage.subject || '(无主题)' }}</h2>
          <div class="detail-meta">
            <div class="meta-avatar" :style="{ background: getAvatarColor(selectedMessage.from_addr) }">
              {{ getInitial(selectedMessage.from_addr) }}
            </div>
            <div class="meta-info">
              <div class="meta-from">{{ selectedMessage.from_addr }}</div>
              <div class="meta-date">{{ formatDetailDate(selectedMessage.date) }}</div>
            </div>
          </div>
        </div>

        <div v-if="selectedMessage.body_html || selectedMessage.body_text" class="detail-content-wrap">
          <div v-html="renderMessageBody(selectedMessage)" class="detail-content" @click="handleMailBodyClick"></div>
        </div>
        <!-- 正文加载中：显示骨架屏 -->
        <div v-else class="body-skeleton">
          <div class="skeleton-line" style="width: 90%"></div>
          <div class="skeleton-line" style="width: 100%"></div>
          <div class="skeleton-line" style="width: 75%"></div>
          <div class="skeleton-line" style="width: 95%"></div>
          <div class="skeleton-line" style="width: 60%"></div>
          <div class="skeleton-line" style="width: 85%"></div>
          <div class="skeleton-line" style="width: 100%"></div>
          <div class="skeleton-line" style="width: 40%"></div>
        </div>

        <!-- 附件列表（放在正文后面，随正文一起滚动） -->
        <div class="attachment-list" v-if="regularAttachments.length > 0">
          <div class="attachment-header">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            <span>附件 ({{ regularAttachments.length }})</span>
          </div>
          <div class="attachment-item" v-for="att in regularAttachments" :key="att.part_number">
            <div class="att-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            </div>
            <div class="att-info">
              <div class="att-name">{{ att.filename || '未命名附件' }}</div>
              <div class="att-meta">
                {{ formatFileSize(att.size) }}
                <span v-if="att.local_path" class="att-local-tag">已下载</span>
              </div>
            </div>
            <div class="attachment-actions-inline">
              <button class="attachment-action" type="button" title="下载到本机" aria-label="下载附件到本机" @click="downloadAttachment(att)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              </button>
              <button class="attachment-action" type="button" title="保存到 NAS" aria-label="保存附件到 NAS" @click="chooseAttachmentNasTarget(att)">NAS</button>
            </div>
          </div>
        </div>
      </div>
      <NasPathPicker v-model="showAttachmentNasPicker" mode="dir" title="选择 NAS 保存目录" @confirm="saveAttachmentToSelectedNas" />
      <ImageViewer
        :open="imageViewerOpen"
        :images="viewerImages"
        :initial-index="viewerInitialIndex"
        @close="imageViewerOpen = false"
      />
    </div>
    <div v-else-if="!isMobile" class="mail-detail mail-detail-empty">
      <UiEmptyState
        title="选择一封邮件"
        description="邮件内容会显示在这里。"
      />
    </div>
    </div>
  </PageFrame>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, onActivated, watch, nextTick } from 'vue';
import { useMailStore } from '../stores/mail';
import { useUIStore } from '../stores/ui';
import api from '../utils/api';
import AccountIcon from '../components/account/AccountIcon.vue';
import { authWindowBlockedMessage, closeAuthWindow, navigateAuthWindow, openAuthWindowSync } from '../utils/oauthWindow';
import { renderThemedMailBody } from '../utils/sanitize';
import { extractName, extractEmails, getInitial, getAvatarColor, formatDate, formatDetailDate, formatFileSize, downloadAttachment as downloadAttachmentFile, saveAttachmentToNas, getFolderCount } from '../utils/mail-helpers';
import type { Attachment, Message } from '../types/mail';
import { useWebSocket } from '../composables/useWebSocket';
import { useSelectMode } from '../composables/useSelectMode';
import { useConfirmAction } from '../composables/useConfirmAction';
import { useContacts } from '../composables/useContacts';
import { buildForwardDraft, buildReplyDraft } from '../composables/useReplyForward';
import { exportMailToPDF } from '../utils/export-pdf';
import AppIcon from '../components/AppIcon.vue';
import PageFrame from '../components/layout/PageFrame.vue';
import UiBadge from '../components/ui/UiBadge.vue';
import UiEmptyState from '../components/ui/UiEmptyState.vue';
import UiIconButton from '../components/ui/UiIconButton.vue';
import UiLoadingState from '../components/ui/UiLoadingState.vue';
import NasPathPicker from '../components/NasPathPicker.vue';
import ImageViewer, { type ViewerImage } from '../components/mail/ImageViewer.vue';

const mailStore = useMailStore();
const uiStore = useUIStore();
const { quickAddContact } = useContacts();
const showAttachmentNasPicker = ref(false);
const attachmentForNas = ref<Attachment | null>(null);
const imageViewerOpen = ref(false);
const viewerImages = ref<ViewerImage[]>([]);
const viewerInitialIndex = ref(0);

function accountDisplayName(account: any) {
  return String(account?.remark || '').trim() || account?.email || '';
}

function folderIconName(folderName: string) {
  const name = String(folderName || '').toLowerCase();
  if (['inbox', '收件箱'].includes(name)) return 'inbox';
  if (['sent', 'sent messages', 'sent items', 'sent mail', '已发送'].includes(name)) return 'send';
  if (name.includes('draft') || name === '草稿箱') return 'draft';
  if (['junk', 'junk email', 'spam', '垃圾邮件'].includes(name)) return 'junk';
  if (['trash', 'deleted', 'deleted items', 'deleted messages', '已删除'].includes(name)) return 'trash';
  if (name.includes('archive') || name === '存档') return 'archive';
  if (name.includes('star') || name === '星标邮件') return 'star';
  return 'folder';
}

const messages = ref<Message[]>([]);
const selectedMessage = ref<Message | null>(null);
const loading = ref(false);
const totalMessages = ref(0);
const currentPage = ref(1);
const pageSize = 50;
const searchKeyword = ref('');
const readFilter = ref('');
const attachmentFilter = ref(false);
const filterCounts = ref({ all: 0, unread: 0, read: 0, attachments: 0 });
const showMobileFilters = ref(false);
const hasActiveFilter = computed(() => readFilter.value !== '' || attachmentFilter.value);
const noReadStateFolder = computed(() => {
  const folder = (mailStore.currentFolder || '').toLowerCase();
  return [
    'sent',
    'sent messages',
    'sent items',
    '[gmail]/sent mail',
    '[google mail]/sent mail',
    'drafts',
    '[gmail]/drafts',
    '[google mail]/drafts',
    '草稿箱',
    'trash',
    'deleted',
    'deleted items',
    'deleted messages',
    '[gmail]/trash',
    '[google mail]/trash',
  ].includes(folder);
});
function folderAliasKey(folder: string): string {
  const value = (folder || '').toLowerCase();
  if (['sent', 'sent messages', 'sent items', 'sent mail', '[gmail]/sent mail', '[google mail]/sent mail', '已发送'].includes(value)) return 'sent';
  if (['drafts', '[gmail]/drafts', '[google mail]/drafts', '草稿箱'].includes(value)) return 'drafts';
  if (['junk', 'junk email', 'spam', '[gmail]/spam', '[google mail]/spam', '垃圾邮件'].includes(value)) return 'junk';
  if (['trash', 'deleted', 'deleted items', 'deleted messages', '[gmail]/trash', '[google mail]/trash', '已删除'].includes(value)) return 'trash';
  if (['inbox', '收件箱'].includes(value)) return 'inbox';
  return value;
}
function foldersMatchForRefresh(eventFolder: string, currentFolder: string): boolean {
  if (!eventFolder || eventFolder === currentFolder) return true;
  return folderAliasKey(eventFolder) === folderAliasKey(currentFolder);
}

function renderMessageBody(message: Message | null) {
  if (!message) return '';
  return renderThemedMailBody(message.body_html, message.body_text);
}

const regularAttachments = computed(() =>
  (selectedMessage.value?.attachments || []).filter((attachment) => !attachment.is_inline),
);

function handleMailBodyClick(event: MouseEvent) {
  const container = event.currentTarget as HTMLElement;
  const target = event.target as Element | null;
  const clickedImage = target?.closest('img') as HTMLImageElement | null;
  if (!clickedImage || !container.contains(clickedImage)) return;

  const images = Array.from(container.querySelectorAll('img'))
    .map((image) => ({
      element: image,
      src: image.currentSrc || image.src,
      alt: image.alt || selectedMessage.value?.subject || '邮件图片',
    }))
    .filter((item) => Boolean(item.src));
  if (!images.length) return;

  const uniqueImages: Array<{ element: HTMLImageElement; src: string; alt: string }> = [];
  const seen = new Set<string>();
  for (const item of images) {
    if (seen.has(item.src)) continue;
    seen.add(item.src);
    uniqueImages.push(item);
  }
  const clickedSrc = clickedImage.currentSrc || clickedImage.src;
  viewerImages.value = uniqueImages.map(({ src, alt }) => ({ src, alt }));
  viewerInitialIndex.value = Math.max(0, uniqueImages.findIndex((item) => item.src === clickedSrc));
  imageViewerOpen.value = true;
}

const isDraftFolder = computed(() => {
  const folder = (mailStore.currentFolder || '').toLowerCase();
  const folderName = mailStore.currentFolderName;
  return ['drafts', '[gmail]/drafts', '[google mail]/drafts', '草稿箱'].includes(folder) || folderName === '草稿箱';
});
const currentFolderUnreadCount = computed(() => {
  const folder = mailStore.folders.find((item) => item.path === mailStore.currentFolder);
  return folder?.unread_count || 0;
});
const syncing = ref(false);
const refreshingLatest = ref(false);
const rebuilding = ref(false);
const syncProgress = ref('');

interface MessagePageCache {
  messages: Message[];
  total: number;
  filterCounts: {
    all: number;
    unread: number;
    read: number;
    attachments: number;
  };
}

// 前端内存页缓存：账号 + 文件夹 + 页码 + 页大小作为 key，切换分类时先秒显旧数据，再后台刷新。
const pageCache = new Map<string, MessagePageCache>();
const totalPages = computed(() => Math.max(1, Math.ceil(totalMessages.value / pageSize)));
const emptyFilterCounts = () => ({ all: 0, unread: 0, read: 0, attachments: 0 });

/** 生成分页页码数组（含省略号） */
const pageNumbers = computed(() => {
  const total = totalPages.value;
  const current = currentPage.value;
  // 总页数 <= 7 时全部显示
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  // 总页数 > 7 时，显示：1 ... 当前附近 ... 末页
  const pages: (number | string)[] = [1];
  if (current > 3) pages.push('...');
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  for (let i = start; i <= end; i++) pages.push(i);
  if (current < total - 2) pages.push('...');
  pages.push(total);
  return pages;
});

/** 跳转到指定页 */
function goPage(page: number) {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return;
  currentPage.value = page;
  loadMessages();
}

// ==================== 筛选 ====================

function setReadFilter(filter: string) {
  if (noReadStateFolder.value && filter) return;
  readFilter.value = filter;
  attachmentFilter.value = false;
  selectedMessage.value = null;
  currentPage.value = 1;
  pageCache.clear();
  loadMessages();
}

function setAttachmentFilter() {
  attachmentFilter.value = !attachmentFilter.value;
  if (attachmentFilter.value) {
    readFilter.value = '';
  }
  selectedMessage.value = null;
  currentPage.value = 1;
  pageCache.clear();
  loadMessages();
}

function applySearch() {
  selectedMessage.value = null;
  currentPage.value = 1;
  pageCache.clear();
  loadMessages();
}

function clearSearch() {
  if (!searchKeyword.value) return;
  searchKeyword.value = '';
  selectedMessage.value = null;
  currentPage.value = 1;
  pageCache.clear();
  loadMessages();
}

// 移动端检测（防抖 + 组件卸载时清理事件监听）
const isMobile = ref(window.innerWidth <= 960);
let resizeTimer: ReturnType<typeof setTimeout> | null = null;
const onResize = () => {
  if (resizeTimer) clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { isMobile.value = window.innerWidth <= 960; }, 150);
};
window.addEventListener('resize', onResize);

async function handleSentMessage(event: Event) {
  const detail = (event as CustomEvent).detail || {};
  if (detail.account_id && detail.account_id !== mailStore.currentAccountId) {
    mailStore.setAccount(detail.account_id);
    await mailStore.loadFolders();
  }
  const sentFolder = mailStore.folders.find((folder) => folder.name === '已发送');
  mailStore.setFolder(sentFolder?.path || 'Sent');
  selectedMessage.value = null;
  currentPage.value = 1;
  readFilter.value = '';
  attachmentFilter.value = false;
  searchKeyword.value = '';
  pageCache.clear();
  await loadMessages();
  await mailStore.loadFolderCounts();
}
window.addEventListener('flymail-sent-message', handleSentMessage);

// iOS风格文件夹弹出层
const showFolderSheet = ref(false);

// 多选模式（使用 composable，全选范围限定为当前页）
const { selectMode, selectedIds, isAllSelected, enterSelectMode, exitSelectMode, toggleSelect, toggleSelectAll } = useSelectMode(() => messages.value.map(m => m.id));

/** 批量删除 */
async function batchDelete() {
  if (selectedIds.value.size === 0) return;
  const count = selectedIds.value.size;
  uiStore.success(`正在删除 ${count} 封邮件...`);
  try {
    await api.post('/messages/batch-delete', {
      message_ids: [...selectedIds.value],
      account_id: mailStore.currentAccountId,
      folder: mailStore.currentFolder,
    });
    exitSelectMode();
    pageCache.clear();
    await loadMessages();
    uiStore.success(`已删除 ${count} 封邮件`);
  } catch (e) {
    console.error('批量删除失败:', e);
    uiStore.error('批量删除失败');
  }
}

/** 批量标记已读 */
async function batchMarkRead() {
  if (selectedIds.value.size === 0) return;
  const count = selectedIds.value.size;
  const unreadSelectedCount = messages.value.filter(m => selectedIds.value.has(m.id) && !m.is_read).length;
  uiStore.success(`正在标记 ${count} 封邮件...`);
  try {
    await api.post('/messages/batch-mark-read', {
      message_ids: [...selectedIds.value],
      account_id: mailStore.currentAccountId,
      folder: mailStore.currentFolder,
    });
    messages.value = messages.value.map(m =>
      selectedIds.value.has(m.id) ? { ...m, is_read: true } : m
    );
    for (let i = 0; i < unreadSelectedCount; i++) {
      updateFilterCountsForReadChange(false, true);
      mailStore.decrementUnreadCount(mailStore.currentFolder);
    }
    exitSelectMode();
    await refreshCurrentListState();
    uiStore.success(`已标记 ${count} 封邮件为已读`);
  } catch (e) {
    console.error('批量标记已读失败:', e);
    uiStore.error('批量标记已读失败');
  }
}

async function markCurrentFolderAllRead() {
  if (!mailStore.currentAccountId || currentFolderUnreadCount.value <= 0) return;
  const confirmed = await uiStore.showConfirm({
    title: '当前文件夹全部已读',
    message: `确定将“${mailStore.currentFolderName}”中的全部未读邮件标记为已读吗？`,
    confirmText: '全部已读',
  });
  if (!confirmed) return;
  try {
    const result = await api.post('/messages/mark-all-read', {
      account_ids: [mailStore.currentAccountId],
      folder: mailStore.currentFolder,
    }) as any;
    pageCache.clear();
    await loadMessages();
    await mailStore.loadFolderCounts();
    uiStore.success(`已标记 ${Number(result.total_marked || 0)} 封邮件为已读`);
  } catch (error: any) {
    uiStore.error(error?.error || error?.message || '全部已读失败');
  }
}

// 请求版本号：防止切换账号时旧请求的响应覆盖新数据
let loadVersion = 0;

// WebSocket 实时同步（使用 composable）
const { wsConnected, connect: connectWs, disconnect: disconnectWs } = useWebSocket(handleWsMessage)

/** 处理 WebSocket 业务消息 */
function handleWsMessage(data: any) {
  if (data.type === 'new_mail') {
    // 全局 App 负责通知入库；邮件页只刷新侧边栏未读计数。
    if (!data.account_id || data.account_id === mailStore.currentAccountId) {
      mailStore.loadFolderCounts();
    }
  } else if (data.type === 'cache_updated') {
    // 缓存同步完成：刷新列表和计数（此时缓存已有新邮件数据）
    if (!data.account_id || data.account_id === mailStore.currentAccountId) {
      if (data.folder_counts) {
        mailStore.updateFolderCounts(data.folder_counts);
      }
      if (foldersMatchForRefresh(data.folder || '', mailStore.currentFolder)) {
        if (selectedMessage.value || hasActiveFilter.value || searchKeyword.value) {
          pageCache.clear();
        } else {
          pageCache.clear();
          loadMessages(true);
        }
      }
      if (!data.folder_counts) {
        mailStore.loadFolderCounts();
      }
    }
  } else if (data.type === 'rebuild_done') {
    // 重建同步完成：后端广播，前端静默刷新列表和计数，结束同步中状态
    if (!data.account_id || data.account_id === mailStore.currentAccountId) {
      pageCache.clear();
      mailStore.loadFolderCounts();
      loadMessages(true);
      rebuilding.value = false;
      syncing.value = false;
      syncProgress.value = '';
      if (data.error) {
        uiStore.error('重建同步失败: ' + data.error);
      }
    }
  } else if (data.type === 'schedule_success' || data.type === 'schedule_failed') {
    // 全局 App 负责通知入库；发送成功时刷新已发送文件夹计数。
    if (data.type === 'schedule_success') {
      mailStore.loadFolderCounts();
    }
  } else if (data.type === 'connection_status') {
    // 账号连接状态变化
    if (data.account_id === mailStore.currentAccountId) {
      if (data.status === 'connected') {
        // 连接恢复，自动重试加载数据（替代 30 秒 setTimeout 轮询）
        if (rebuilding.value) return; // 重建同步中不干扰
        mailStore.reauthAccountIds.delete(data.account_id);
        pageCache.clear();
        loadMessages(true);
        mailStore.loadFolderCounts();
      }
    }
    // 任何账号的 reauth_needed 都记录（不限于当前账号）
    if (data.status === 'reauth_needed' && data.account_id) {
      mailStore.reauthAccountIds.add(data.account_id);
    }
  } else if (data.type === 'sync_progress') {
    // 同步进度更新
    if (data.account_id === mailStore.currentAccountId && rebuilding.value) {
      syncProgress.value = `同步中 (${data.completed}/${data.total})`;
    }
  } else if (data.type === 'message_state_changed') {
    if (data.account_id === mailStore.currentAccountId) {
      if (data.action === 'mark_read' || data.action === 'mark_unread') {
        const isRead = data.action === 'mark_read';
        for (const uid of data.uids) {
          const msg = messages.value.find(m => String(m.uid) === String(uid));
          if (msg) {
            updateFilterCountsForReadChange(!!msg.is_read, isRead);
            msg.is_read = isRead;
          }
        }
      } else if (data.action === 'delete' || data.action === 'move') {
        messages.value = messages.value.filter(m => !data.uids.includes(String(m.uid)));
      }
      if (selectedMessage.value || hasActiveFilter.value || searchKeyword.value) {
        refreshCurrentListCounts();
      } else {
        refreshCurrentListState();
      }
    }
  }
}

function updateFilterCountsForReadChange(wasRead: boolean, isRead: boolean) {
  if (wasRead === isRead) return;
  if (!wasRead && isRead) {
    filterCounts.value = {
      ...filterCounts.value,
      unread: Math.max(0, filterCounts.value.unread - 1),
      read: filterCounts.value.read + 1,
    };
    if (readFilter.value === 'unread') {
      totalMessages.value = Math.max(0, totalMessages.value - 1);
    }
  } else if (wasRead && !isRead) {
    filterCounts.value = {
      ...filterCounts.value,
      unread: filterCounts.value.unread + 1,
      read: Math.max(0, filterCounts.value.read - 1),
    };
    if (readFilter.value === 'read') {
      totalMessages.value = Math.max(0, totalMessages.value - 1);
    }
  }
}

async function refreshCurrentListState() {
  pageCache.clear();
  selectedMessage.value = null;
  await loadMessages();
  await mailStore.loadFolderCounts();
}

async function refreshCurrentListCounts() {
  pageCache.clear();
  await loadMessages();
  await mailStore.loadFolderCounts();
}

function getPageCacheKey() {
  return [
    mailStore.currentAccountId,
    mailStore.currentFolder,
    currentPage.value,
    pageSize,
    readFilter.value || 'all',
    attachmentFilter.value ? 'attachments' : 'no-attachments-filter',
    searchKeyword.value || 'no-search',
  ].join('::');
}

function normalizeMessagesForDisplay(items: Message[]) {
  if (noReadStateFolder.value || filterCounts.value.unread !== 0) return items;
  return items.map((item) => item.is_read ? item : { ...item, is_read: true });
}

function resetVisibleListState() {
  messages.value = [];
  totalMessages.value = 0;
  filterCounts.value = emptyFilterCounts();
  selectedMessage.value = null;
}

/** 缓存当前页数据 */
function saveCurrentPageCache(data: any) {
  pageCache.set(getPageCacheKey(), {
    messages: data.messages || [],
    total: data.total || 0,
    filterCounts: data.filter_counts || { all: 0, unread: 0, read: 0, attachments: 0 },
  });
}

watch(
  () => [mailStore.currentFolder, mailStore.currentAccountId],
  () => {
    resetVisibleListState();
    currentPage.value = 1;
    readFilter.value = '';
    attachmentFilter.value = false;
    pageCache.clear();
    loadVersion++;
    loadMessages();
  }
);

async function openPendingMessage() {
  const raw = sessionStorage.getItem('flymail_pending_message');
  if (!raw) return false;
  sessionStorage.removeItem('flymail_pending_message');
  try {
    const pending = JSON.parse(raw);
    if (!pending?.account_id || !pending?.id) return false;
    if (mailStore.currentAccountId !== pending.account_id) {
      mailStore.setAccount(pending.account_id);
      await mailStore.loadFolders();
    }
    mailStore.setFolder(pending.folder || 'INBOX');
    await loadMessages();
    const target = messages.value.find((item: Message) =>
      item.id === pending.id || (pending.uid && Number(item.uid) === Number(pending.uid))
    ) || {
      id: pending.id,
      uid: pending.uid,
      subject: '',
      from_addr: '',
      date: '',
      is_read: true,
      folder: pending.folder || 'INBOX',
      account_id: pending.account_id,
    };
    await selectMessage(target as Message);
    return true;
  } catch (error) {
    console.error('打开聚合邮件失败:', error);
    return false;
  }
}

function openMobileSidebar() {
  window.dispatchEvent(new CustomEvent('flymail-toggle-sidebar'));
}

async function handleMailNavigation(event: Event) {
  const detail = (event as CustomEvent).detail as { type?: string; id?: string; path?: string } | undefined;
  if (!detail) return;
  if (detail.type === 'account' && detail.id) {
    if (detail.id !== mailStore.currentAccountId) await switchAccount(detail.id);
    return;
  }
  if (detail.type === 'reauth' && detail.id) {
    await reauthorize(detail.id);
    return;
  }
  if (detail.type === 'folder' && detail.path) {
    if (detail.path === mailStore.currentFolder) return;
    searchKeyword.value = '';
    mailStore.setFolder(detail.path);
  }
}

onMounted(async () => {
  connectWs();
  window.addEventListener('flymail-mail-navigation', handleMailNavigation);
  if (!(await openPendingMessage())) await loadMessages();
});

onActivated(async () => {
  if (await openPendingMessage()) return;
  if (selectedMessage.value) {
    mailStore.loadFolderCounts();
    return;
  }
  if (!selectedMessage.value && messages.value.length === 0) {
    loadMessages();
    return;
  }
  loadMessages(true);
  mailStore.loadFolderCounts();
});

onUnmounted(() => {
  disconnectWs();
  // 清理 resize 事件监听，防止内存泄漏
  window.removeEventListener('resize', onResize);
  window.removeEventListener('flymail-sent-message', handleSentMessage);
  window.removeEventListener('flymail-mail-navigation', handleMailNavigation);
  if (resizeTimer) { clearTimeout(resizeTimer); resizeTimer = null; }
});

/** 切换账号 */
async function switchAccount(id: string) {
  mailStore.setAccount(id);
  readFilter.value = '';
  attachmentFilter.value = false;
  searchKeyword.value = '';
  await mailStore.loadFolders();
  pageCache.clear();
  selectedMessage.value = null;
  currentPage.value = 1;
  await loadMessages();
}

function navigateToCompose() {
  window.dispatchEvent(new CustomEvent('flymail-navigate', { detail: 'compose' }));
}

function openCompose() {
  mailStore.setComposeDraft({
    account_id: mailStore.currentAccountId,
  });
  navigateToCompose();
}

async function refreshLatestPage() {
  if (refreshingLatest.value || syncing.value || rebuilding.value || !mailStore.currentAccountId) return;
  refreshingLatest.value = true;
  syncing.value = true;
  try {
    const params: Record<string, string | number> = {
      folder: mailStore.currentFolder,
      page_size: pageSize,
    };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    await api.get('/messages/refresh', { params });
    currentPage.value = 1;
    pageCache.clear();
    await loadMessages();
    await mailStore.loadFolderCounts();
  } catch (e) {
    console.error('刷新邮件失败:', e);
    uiStore.error('刷新邮件失败');
  } finally {
    refreshingLatest.value = false;
    syncing.value = false;
  }
}

/** 重新授权指定账号（复用添加账号的 OAuth 流程） */
async function reauthorize(accountId?: string) {
  const targetId = accountId || mailStore.currentAccountId;
  const targetAccount = mailStore.accounts.find((a: any) => a.id === targetId);
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
    let redirectUri = '';
    if (provider === 'outlook') {
      redirectUri = settings.outlook_redirect_uri || '';
      if (!redirectUri) { closeAuthWindow(authWindow); uiStore.error('请先在设置页面配置 Microsoft 重定向 URI'); return; }
    } else {
      redirectUri = settings.gmail_redirect_uri || '';
      if (!redirectUri) { closeAuthWindow(authWindow); uiStore.error('请先在设置页面配置 Gmail 重定向 URI'); return; }
    }
    // 标记这是重新授权，OAuth 回调后不跳转到账号页
    sessionStorage.setItem('flymail_oauth_reauth', '1');
    const data = await api.post('/accounts/auth-url', { provider, redirect_uri: redirectUri }) as any;
    if (data.error) { closeAuthWindow(authWindow); uiStore.error('获取授权链接失败：' + data.error); return; }
    if (data.auth_url) {
      if (!navigateAuthWindow(authWindow, data.auth_url)) uiStore.error(authWindowBlockedMessage(providerLabel));
    } else {
      closeAuthWindow(authWindow);
      uiStore.error('获取授权链接失败');
    }
  } catch (e: any) {
    closeAuthWindow(authWindow);
    uiStore.error('重新授权失败：' + (e.response?.data?.error || e.message || '网络错误'));
  }
}

// 删除确认（使用 composable，两次点击确认机制）
const { confirmTarget: deleteConfirm, requestConfirm: onDeleteConfirm } = useConfirmAction()

/** 删除邮件（两次点击确认机制） */
async function onDeleteMessage() {
  if (!selectedMessage.value) return;
  // 第一次点击进入确认状态，第二次点击执行删除
  if (!onDeleteConfirm(selectedMessage.value.id)) return;

  try {
    const params: Record<string, string> = { folder: mailStore.currentFolder };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    await api.delete(`/messages/${selectedMessage.value!.id}`, { params });
    selectedMessage.value = null;
    // 清除所有页缓存，避免翻页时显示旧的缓存数据
    pageCache.clear();
    // 删除后重新加载当前页，让后端返回正确的分页数据（自动补充新邮件）
    await loadMessages();
  } catch (e) {
    console.error('删除邮件失败:', e);
    uiStore.error('删除邮件失败');
  }
}

/** 加载邮件列表（带竞态保护：只接受最新请求的结果） */
async function loadMessages(preserveVisible = false) {
  if (!preserveVisible) messages.value = [];
  if (!mailStore.currentAccountId) {
    resetVisibleListState();
    loading.value = false;
    syncing.value = false;
    return;
  }
  loading.value = true;
  syncing.value = true;
  const version = ++loadVersion;
  try {
    const params: Record<string, string | number> = {
      folder: mailStore.currentFolder,
      page: currentPage.value,
      page_size: pageSize,
    };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    if (readFilter.value) params.read_filter = readFilter.value;
    if (attachmentFilter.value) params.attachment_filter = 'true';
    if (searchKeyword.value) params.keyword = searchKeyword.value;
    const endpoint = searchKeyword.value ? '/messages/search' : '/messages';
    const data = await api.get(endpoint, { params }) as any;
    // 只接受最新版本的结果，丢弃旧请求的响应
    if (version !== loadVersion) return;
    // Outlook 连接异常时，后端返回 reconnecting: true，前端展示友好提示
    if (data.reconnecting) {
      uiStore.error('邮箱连接异常，正在重新连接，请稍后再试');
      return;
    }
    const nextMessages = data.messages || [];
    const nextTotal = data.total || 0;
    const nextTotalPages = Math.max(1, Math.ceil(nextTotal / pageSize));
    if (currentPage.value > nextTotalPages && nextTotal > 0) {
      currentPage.value = nextTotalPages;
      return await loadMessages(preserveVisible);
    }
    saveCurrentPageCache(data);
    totalMessages.value = nextTotal;
    // 更新筛选计数
    if (data.filter_counts) {
      filterCounts.value = data.filter_counts;
    }
    messages.value = normalizeMessagesForDisplay(nextMessages);
    // 用 list_messages API 返回的数据更新侧边栏文件夹计数
    // 收件箱显示未读数，其他文件夹显示邮件总数
  } catch (e) {
    if (version !== loadVersion) return;
    console.error('加载邮件失败:', e);
    uiStore.error('加载邮件失败');
  } finally {
    if (version === loadVersion) {
      loading.value = false;
      // 重建同步期间不重置 syncing（由 rebuild_done WebSocket 消息控制）
      if (!rebuilding.value) syncing.value = false;
      // 列表加载完成后，后台批量预取当前页邮件正文
      nextTick(() => { prefetchVisibleMessages(); });
    }
  }
}

/** 选择邮件查看详情（带竞态保护）
 * 用户点击邮件时：
 * 1. 用 BODY.PEEK[] 拉取正文（不自动标已读）
 * 2. 如果邮件未读，调用 STORE +FLAGS \Seen 标记已读（同步到邮箱服务器）
 * 3. 更新本地列表中的已读状态和侧边栏未读数
 */
async function selectMessage(msg: Message) {
  const version = ++loadVersion;

  if (isDraftFolder.value) {
    try {
      const params: Record<string, string> = { folder: mailStore.currentFolder };
      if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
      const data = await api.get(`/messages/${msg.id}`, { params }) as any;
      if (version !== loadVersion) return;
      mailStore.setComposeDraft({
        to: parseAddressList(data.to_addr || msg.to_addr || ''),
        subject: data.subject || msg.subject || '',
        body_html: data.body_html || data.body_text || '',
        account_id: mailStore.currentAccountId,
        draft_message_id: msg.id,
        draft_folder: mailStore.currentFolder,
      });
      navigateToCompose();
    } catch (e) {
      if (version !== loadVersion) return;
      console.error('加载草稿失败:', e);
      uiStore.error('加载草稿失败');
    }
    return;
  }

  // 乐观 UI：立即用摘要数据渲染头部，正文留空显示骨架屏
  selectedMessage.value = {
    ...msg,
    body_html: '',
    body_text: '',
    attachments: [],
  };

  try {
    const params: Record<string, string> = { folder: mailStore.currentFolder };
    if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
    const data = await api.get(`/messages/${msg.id}`, { params }) as any;
    if (version !== loadVersion) return;
    // 用完整数据替换（正文填充）
    selectedMessage.value = data;

    // 未读邮件：调用 IMAP STORE +FLAGS \Seen 标记已读，同步到邮箱服务器
    if (!msg.is_read) {
      const wasRead = !!msg.is_read;
      // 直接修改 messages 数组中对应项的 is_read，确保 Vue 响应式追踪
      const idx = messages.value.findIndex((m: Message) => m.id === msg.id);
      if (idx !== -1) {
        messages.value[idx] = { ...messages.value[idx], is_read: true };
      }
      if (selectedMessage.value) {
        selectedMessage.value = { ...selectedMessage.value, is_read: true };
      }
      updateFilterCountsForReadChange(wasRead, true);
      // 异步调用标记已读API，不阻塞界面
      api.post('/mark-read', {
        message_id: msg.id,
        folder: mailStore.currentFolder,
        account_id: mailStore.currentAccountId || '',
      }).catch((e: any) => console.error('[FlyMail] 标记已读失败:', e));

      // 更新侧边栏未读数（收件箱减1）
      mailStore.decrementUnreadCount(mailStore.currentFolder);
      refreshCurrentListCounts();
    }
  } catch (e: any) {
    if (version !== loadVersion) return;
    console.error('加载邮件详情失败:', e);
    // Outlook 连接异常时，后端返回 503 + { reconnecting: true }
    const respData = e?.response?.data;
    if (respData?.reconnecting) {
      uiStore.error('邮箱连接异常，正在重新连接，请稍后再试');
    } else {
      uiStore.error('加载邮件详情失败');
    }
  }
}

function backToList() {
  selectedMessage.value = null;
}

// ==================== 左滑返回手势 ====================
let _touchStartX = 0;
let _touchStartY = 0;
let _touchStartTime = 0;

function onDetailTouchStart(e: TouchEvent) {
  _touchStartX = e.touches[0].clientX;
  _touchStartY = e.touches[0].clientY;
  _touchStartTime = Date.now();
}

function onDetailTouchMove(_e: TouchEvent) {
  // 不阻止默认行为，允许正常滚动
}

function onDetailTouchEnd(e: TouchEvent) {
  const dx = e.changedTouches[0].clientX - _touchStartX;
  const dy = Math.abs(e.changedTouches[0].clientY - _touchStartY);
  const dt = Date.now() - _touchStartTime;
  // 左滑条件：水平滑动 > 80px，垂直偏移 < 100px，时间 < 500ms
  if (dx > 80 && dy < 100 && dt < 500) {
    backToList();
  }
}

/** 回复邮件：按原发件人、收件人和抄送人角色构建回复列表。 */
function replyMessage() {
  if (!selectedMessage.value) return;
  const account = mailStore.accounts.find((item: any) => item.id === mailStore.currentAccountId);
  mailStore.setComposeDraft(buildReplyDraft(
    selectedMessage.value,
    String(account?.email || '').toLowerCase(),
    mailStore.currentAccountId,
  ));
  navigateToCompose();
}

/** 转发邮件：净化原始正文与元数据后构建草稿。 */
function forwardMessage() {
  if (!selectedMessage.value) return;
  mailStore.setComposeDraft(buildForwardDraft(selectedMessage.value, mailStore.currentAccountId));
  navigateToCompose();
}

async function addSenderToContacts() {
  if (!selectedMessage.value) return;
  const email = extractEmails(selectedMessage.value.from_addr || '')[0] || '';
  if (!email) {
    uiStore.error('无法识别发件人邮箱');
    return;
  }
  try {
    await quickAddContact(extractName(selectedMessage.value.from_addr), email);
    uiStore.success('已添加到联系人');
  } catch (error: any) {
    if (error?.status_code === 409 || error?.status === 409 || String(error?.error || error?.message || '').includes('已在联系人')) {
      uiStore.info('该邮箱已在联系人中');
    } else {
      uiStore.error(error?.error || error?.message || '添加联系人失败');
    }
  }
}

async function exportSelectedMessage() {
  if (!selectedMessage.value) return;
  try {
    await exportMailToPDF(selectedMessage.value);
  } catch (error: any) {
    uiStore.error(error?.message || '导出 PDF 失败');
  }
}

function parseAddressList(value: string): string[] {
  return String(value || '')
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

// 悬停预取：鼠标悬停时静默预取邮件正文，点击时大概率已缓存
let _prefetchTimer: ReturnType<typeof setTimeout> | null = null;
function prefetchMessage(msg: Message) {
  if (!mailStore.currentAccountId) return;
  if (_prefetchTimer) return; // 防抖：300ms 内只预取一封
  _prefetchTimer = setTimeout(() => { _prefetchTimer = null; }, 300);
  const params: Record<string, string> = { folder: mailStore.currentFolder };
  if (mailStore.currentAccountId) params.account_id = mailStore.currentAccountId;
  api.get(`/messages/${msg.id}`, { params }).catch(() => {});
}

// 列表加载完成后，后台批量预取当前页邮件正文
function prefetchVisibleMessages() {
  if (!mailStore.currentAccountId) return;
  const ids = messages.value.slice(0, 10).map((m: Message) => m.id);
  if (ids.length === 0) return;
  api.post('/prefetch-messages', {
    message_ids: ids,
    folder: mailStore.currentFolder,
    account_id: mailStore.currentAccountId || '',
  }).catch(() => {});
}

/** 下载附件（适配器：模板只传 Attachment，补全消息上下文后调用公共工具函数） */
function downloadAttachment(att: Attachment) {
  const msg = selectedMessage.value;
  if (!msg) return;
  downloadAttachmentFile({
    messageId: msg.id,
    accountId: msg.account_id || mailStore.currentAccountId || '',
    folder: msg.folder || 'INBOX',
    partNumber: att.part_number,
    filename: att.filename || 'attachment',
  });
}

function chooseAttachmentNasTarget(att: Attachment) {
  attachmentForNas.value = att;
  showAttachmentNasPicker.value = true;
}

async function saveAttachmentToSelectedNas(targetDir: string) {
  const msg = selectedMessage.value;
  const att = attachmentForNas.value;
  showAttachmentNasPicker.value = false;
  attachmentForNas.value = null;
  if (!msg || !att) return;
  try {
    const result = await saveAttachmentToNas({
      messageId: msg.id,
      accountId: msg.account_id || mailStore.currentAccountId || '',
      folder: msg.folder || 'INBOX',
      partNumber: att.part_number,
      targetDir,
      filename: att.filename || 'attachment',
    });
    uiStore.success(`附件已保存到 ${result.path}`);
  } catch (error: any) {
    uiStore.error(error?.error || error?.message || '保存附件到 NAS 失败');
  }
}
</script>

<style scoped>
.mail-view {
  width: 100%;
  min-width: 0;
  min-height: 0;
}

.mail-view,
.mail-shell,
.mail-list {
  box-sizing: border-box;
}

.mail-shell {
  width: 100%;
  min-width: 0;
}

.mail-list {
  min-width: 0;
  width: 100%;
}

.detail-body,
.detail-content-wrap,
.detail-content {
  min-width: 0;
}

.detail-content {
  width: 100%;
  box-sizing: border-box;
}

.mail-item {
  box-sizing: border-box;
}

.mail-item:not(.unread) {
  background: var(--ui-fill-muted);
}

.mail-item:not(.unread) .mail-from,
.mail-item:not(.unread) .mail-subject,
.mail-item:not(.unread) .mail-date {
  color: var(--text-tertiary);
}

.mail-item:not(.unread) .mail-avatar {
  opacity: 0.76;
}

.mail-item:not(.unread):hover {
  background: var(--ui-fill-hover);
}

.toolbar-right {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.search-input {
  min-width: 0;
}

.mail-main-row {
  min-width: 0;
}

.mail-info {
  min-width: 0;
}

@media (max-width: 768px) {
  .mail-view {
    overflow: visible;
  }

  .mail-list,
  .mail-detail {
    width: 100%;
  }

  .list-toolbar {
    flex-wrap: wrap;
    align-items: stretch;
  }

  .toolbar-right {
    width: 100%;
    flex-wrap: nowrap;
    justify-content: flex-start;
  }

  .search-input {
    flex: 1;
    width: auto;
    min-width: 0;
  }

  .list-items {
    overflow-x: auto;
  }

  .mail-item {
    display: flex;
    align-items: center;
    min-width: 560px;
    width: max-content;
    padding: 10px 12px;
  }

  .mail-sender {
    width: 88px;
    min-width: 88px;
    gap: 8px;
    padding-right: 8px;
  }

  .mail-info {
    min-width: 0;
  }

  .mail-main-row {
    gap: 6px;
    min-width: 0;
  }

  .mail-status-tag {
    width: 40px;
    margin-left: 0;
  }

  .mail-date {
    width: 54px;
    margin-left: 0;
  }

  .pagination {
    position: sticky;
    bottom: 0;
    left: 0;
    right: 0;
  }

  .page-btn.page-nav {
    min-width: 84px;
    padding: 0 8px;
  }

  .detail-body {
    overflow-x: auto;
    overflow-y: auto;
  }

  .detail-content {
    padding: var(--space-3) var(--space-4);
    min-width: 0;
  }

  .detail-content :deep(table) {
    min-width: 100%;
    width: max-content;
  }
}

.mail-view {
  position: relative;
  width: 100%;
  min-width: 0;
  min-height: 0;
}

.mail-shell {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 16px;
  overflow: hidden;
}

.mail-shell.detail {
  grid-template-columns: 220px minmax(0, 1fr);
}

.folder-sidebar {
  min-height: 0;
  overflow-y: auto;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 10px;
}

.folder-nav-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: none;
  background: transparent;
  border-radius: 10px;
  padding: 10px 12px;
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}

.folder-nav-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.folder-nav-item.active {
  background: var(--ui-fill-selected);
  color: var(--ui-accent);
  font-weight: 600;
}

.folder-nav-name,
.folder-nav-count {
  min-width: 0;
  white-space: nowrap;
}

.folder-nav-name {
  overflow: hidden;
  text-overflow: ellipsis;
}

.folder-nav-count {
  color: var(--text-tertiary);
  font-size: 12px;
  flex-shrink: 0;
}

/* 账号 Tab 切换 */
/* 重新授权提示条 */
.reauth-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: var(--ui-warning-soft);
  border-bottom: 1px solid color-mix(in srgb, var(--ui-warning) 28%, var(--ui-border));
  font-size: var(--text-sm);
  color: var(--ui-warning);
  flex-shrink: 0;
}

.reauth-banner .btn-sm {
  padding: 3px 12px;
  font-size: 12px;
}

.account-tabs {
  display: flex;
  gap: var(--space-1);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-secondary);
  flex-shrink: 0;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  flex-wrap: nowrap;
  -webkit-overflow-scrolling: touch;
}

.account-tab {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 14px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: var(--border-radius-full);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast), color var(--transition-fast);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  white-space: nowrap;
  min-width: 0;
  max-width: 220px;
}

.account-tab:hover {
  background: var(--bg-hover);
}

.account-tab.active {
  background: var(--bg-active);
  border-color: var(--color-accent);
  color: var(--color-accent);
  font-weight: var(--font-medium);
}

.account-tab-wrapper {
  display: flex;
  align-items: center;
  gap: 2px;
  flex: 0 0 auto;
  min-width: 0;
}

.btn-reauth {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  border-radius: 50%;
  cursor: pointer;
  color: var(--ui-warning);
  transition: background var(--transition-fast), color var(--transition-fast);
  padding: 0;
}

.btn-reauth:hover {
  background: var(--ui-warning-soft);
  color: var(--ui-warning);
}

.account-email {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

/* ==================== 邮件列表 ==================== */
.mail-list {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
}

/* 普通模式工具栏 */
.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  min-width: 0;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 0 1 auto;
  min-width: 0;
}

.search-input {
  max-width: 100%;
  width: 220px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}

.search-input:focus {
  border-color: var(--ui-accent);
  box-shadow: 0 0 0 3px var(--ui-focus-ring);
}

/* 筛选按钮（工具栏内） */
.filter-btn {
  padding: 3px 10px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.filter-btn:hover {
  background: var(--bg-hover);
  color: var(--text-secondary);
}
.filter-btn.active {
  background: var(--ui-fill-selected);
  color: var(--ui-accent);
  font-weight: 500;
}

/* 工具栏分隔线（筛选按钮和同步按钮之间） */
.toolbar-divider {
  width: 1px;
  height: 16px;
  background: var(--border-color);
  flex-shrink: 0;
  margin: 0 4px;
}

/* 图标按钮（多选入口） */
.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  flex-shrink: 0;
}

.btn-icon:hover {
  background: var(--bg-hover);
  color: var(--accent-blue);
}

.btn-icon:active {
  background: var(--ui-fill-selected);
  color: var(--ui-accent);
}

.refresh-button svg {
  transform-origin: center;
}

.refresh-button.is-refreshing svg {
  animation: spin 0.8s linear infinite;
}

/* 重建同步按钮旋转动画 */
/* 多选模式工具栏 */
.select-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 12px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.select-info {
  flex: 1;
  font-size: var(--text-sm);
  color: var(--text-secondary);
  font-weight: 500;
}

.select-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 多选工具栏图标按钮 */
.select-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, opacity 0.15s;
}

.select-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.select-btn:active {
  background: var(--bg-active);
}

.select-btn.delete {
  color: var(--ui-danger);
}

.select-btn.delete:hover {
  background: var(--ui-danger-soft);
  color: var(--ui-danger);
}

.select-btn.delete:disabled {
  color: var(--text-tertiary);
  opacity: 0.4;
  cursor: not-allowed;
}

.select-btn.delete:disabled:hover {
  background: transparent;
}

/* 标记已读按钮 */
.select-btn.mark-read {
  color: var(--ui-accent);
}

.select-btn.mark-read:hover {
  background: var(--ui-fill-selected);
  color: var(--ui-accent);
}

.select-btn.mark-read:disabled {
  color: var(--text-tertiary);
  opacity: 0.4;
  cursor: not-allowed;
}

.select-btn.mark-read:disabled:hover {
  background: transparent;
}

/* 实时同步状态指示器 */
.sync-badge {
  font-size: 11px;
  color: var(--ui-accent);
  background: var(--ui-fill-selected);
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.sync-status {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  opacity: 0.4;
  transition: background var(--transition-normal), opacity var(--transition-normal), box-shadow var(--transition-normal);
}

.sync-status.connected .status-dot {
  background: var(--color-success);
  opacity: 1;
  box-shadow: 0 0 0 3px var(--ui-success-soft);
}

.list-count {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  font-weight: var(--font-medium);
  white-space: nowrap;
  flex-shrink: 0;
}

.list-loading,
.list-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  color: var(--text-tertiary);
  font-size: var(--text-sm);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border-color);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 邮件列表项 */
.list-items {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior: contain;
}

.mail-item {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 10px 16px;
  border: none;
  background: transparent;
  border-bottom: 1px solid var(--border-color);
  cursor: pointer;
  transition: background var(--transition-fast);
  width: 100%;
  min-width: 640px;
  text-align: left;
  font-family: inherit;
  min-height: 52px;
}

/* 左列：头像 + 发件人（固定宽度，保证各行对齐） */
.mail-sender {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
  width: 160px;
  min-width: 0;
  padding-right: 12px;
}

.mail-item:hover {
  background: var(--bg-hover);
}

.mail-item.unread .mail-from {
  font-weight: var(--font-semibold);
  color: var(--text-primary);
}

.mail-item.unread .mail-subject {
  color: var(--text-primary);
  font-weight: var(--font-medium);
}

/* 中列：状态图标 + 主题 + 日期（弹性宽度，自动填充剩余空间） */
.mail-main-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

/* 已读/未读邮件状态图标 */
.mail-status-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}
.unread-icon {
  color: var(--ui-warning);
}
.read-icon {
  color: var(--ui-text-3);
}

/* 附件图标（列表中的回形针标记） */
.att-badge {
  flex-shrink: 0;
  color: var(--ui-text-3);
  margin-left: 2px;
}

/* 多选模式选中行高亮 */
.mail-item.selected {
  background: var(--ui-fill-selected);
}

/* 勾选圆圈 */
.check-circle {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid var(--ui-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: background 0.2s cubic-bezier(0.34, 1.56, 0.64, 1), border-color 0.2s cubic-bezier(0.34, 1.56, 0.64, 1), transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
  margin-right: 10px;
}

.check-circle.checked {
  background: var(--ui-accent);
  border-color: var(--ui-accent);
  transform: scale(1.1);
}

/* ==================== 分页器 ==================== */
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 10px 16px;
  border-top: 1px solid var(--border-color);
  flex-shrink: 0;
  background: var(--bg-primary);
  position: sticky;
  bottom: 0;
  z-index: 2;
}

.page-nav {
  gap: 4px;
}

.page-mobile-summary {
  display: inline-flex;
  align-items: baseline;
  justify-content: center;
  min-width: 72px;
  padding: 0 8px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.page-mobile-current {
  color: var(--text-primary);
  font-size: 16px;
}

.page-mobile-divider,
.page-mobile-total {
  color: var(--text-tertiary);
}

.page-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, opacity 0.15s;
  font-family: inherit;
  padding: 0 6px;
}

.page-btn:hover:not(:disabled):not(.active) {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.page-btn:active:not(:disabled) {
  background: var(--bg-active);
}

.page-btn.active {
  background: var(--ui-accent);
  color: var(--ui-text-inverse);
  font-weight: 600;
}

.page-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.page-ellipsis {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 32px;
  color: var(--text-tertiary);
  font-size: 13px;
  user-select: none;
}

.mail-avatar {
  width: 34px;
  height: 34px;
  border-radius: var(--border-radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ui-text-inverse);
  font-size: 13px;
  font-weight: var(--font-semibold);
  flex-shrink: 0;
}

.mail-info {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}

.mail-from {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: var(--font-medium);
  flex: 1;
  min-width: 0;
}

.mail-subject {
  font-size: 13px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

.mail-date {
  font-size: 11px;
  color: var(--text-tertiary);
  flex-shrink: 0;
  white-space: nowrap;
  width: 64px;
  text-align: right;
}

/* 已读/未读标签（日期前一列，固定宽度） */
.mail-status-tag {
  flex-shrink: 0;
  width: 42px;
  text-align: center;
  font-size: 10px;
  font-weight: 500;
  padding: 1px 0;
  border-radius: 4px;
  line-height: 1.5;
  white-space: nowrap;
}
.mail-status-tag.unread {
  background: var(--ui-warning-soft);
  color: var(--ui-warning);
}
.mail-status-tag.read {
  background: var(--ui-fill-muted);
  color: var(--ui-text-3);
}

/* ==================== 邮件详情 ==================== */
.mail-detail {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
}

.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.btn-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  border-radius: var(--border-radius-sm);
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.btn-back:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.btn-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: none;
  border-radius: var(--border-radius-sm);
  background: var(--bg-secondary);
  color: var(--text-secondary);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.btn-action:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.btn-action.confirm {
  background: var(--ui-danger);
  color: var(--ui-text-inverse);
}

.btn-action.confirm:hover {
  background: var(--ui-danger-hover);
}

.detail-header {
  padding: var(--space-4) var(--space-5) var(--space-3);
  border-bottom: 1px solid var(--border-color);
}

.detail-subject {
  font-size: var(--text-xl);
  font-weight: var(--font-semibold);
  color: var(--text-primary);
  line-height: 1.3;
  margin-bottom: var(--space-3);
}

.detail-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.meta-avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--border-radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ui-text-inverse);
  font-size: var(--text-sm);
  font-weight: var(--font-semibold);
  flex-shrink: 0;
}

.meta-info {
  min-width: 0;
}

.meta-from {
  font-size: var(--text-sm);
  color: var(--text-primary);
  font-weight: var(--font-medium);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-date {
  font-size: var(--text-xs);
  color: var(--text-tertiary);
  margin-top: 2px;
}

.detail-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: auto;
  min-height: 0;
  min-width: 0;
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--text-primary);
  -webkit-overflow-scrolling: touch;
}

.detail-content-wrap {
  min-width: 0;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

/* 正文内容区域 */
.detail-content {
  padding: var(--space-5);
  min-width: min-content;
}

.detail-content :deep(*) {
  max-width: 100%;
}

.detail-content :deep(table) {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
}

.detail-content :deep(pre),
.detail-content :deep(blockquote) {
  overflow-x: auto;
}

/* 邮件正文中的图片和链接 */
.detail-body :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: var(--border-radius-md);
  cursor: zoom-in;
}

.detail-body :deep(a) {
  color: var(--color-accent);
  text-decoration: none;
}

.detail-body :deep(a:hover) {
  text-decoration: underline;
}

/* 附件列表 */
.attachment-list {
  margin-top: 16px;
  padding: 12px;
  border-top: 1px solid var(--ui-border);
}
.attachment-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ui-text-2);
  margin-bottom: 8px;
}
.attachment-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--ui-surface-2);
  cursor: pointer;
  transition: background 0.15s;
}
.attachment-item:hover {
  background: var(--ui-fill-hover);
}
.att-icon {
  flex-shrink: 0;
  color: var(--ui-text-3);
}
.att-info {
  flex: 1;
  min-width: 0;
}
.att-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ui-text-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.att-meta {
  font-size: 11px;
  color: var(--ui-text-3);
  margin-top: 2px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.att-local-tag {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  background: var(--ui-success-soft);
  color: var(--ui-success);
  font-size: 11px;
  font-weight: 600;
}
.attachment-actions-inline {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
}

/* 移动端：筛选切换按钮（桌面端隐藏） */
.mobile-filter-toggle { display: none; }

/* 移动端适配 */
@media (max-width: 768px) {
  .mail-shell {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
    width: 100%;
    max-width: 100%;
  }

  .mail-shell.detail {
    grid-template-columns: minmax(0, 1fr);
  }

  .mail-view {
    min-height: 0;
  }

  .mail-list,
  .mail-detail {
    width: 100%;
    max-width: 100%;
    border-radius: 0;
    border-left: none;
    border-right: none;
  }

  /* 移动端缩小发件人列宽度，给主题更多空间 */
  .mail-sender {
    width: 88px;
    gap: 10px;
    padding-right: 8px;
  }

  /* 工具栏精简：只保留多选+文件夹+筛选图标+同步 */
  .list-toolbar {
    position: relative;
    padding: 8px 12px;
    gap: 8px;
    align-items: center;
    flex-direction: row;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .toolbar-left .toolbar-divider { display: none; }
  .toolbar-left .filter-btn { display: inline-flex; }
  .toolbar-left {
    width: auto;
    flex: 0 0 auto;
    flex-wrap: nowrap;
    min-width: 0;
    overflow: visible;
    gap: 6px;
  }
  .toolbar-left .filter-btn {
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    min-height: 28px;
    padding: 4px 9px;
  }
  .toolbar-right {
    width: auto;
    gap: 6px;
    flex: 0 0 auto;
    justify-content: flex-end;
  }

  /* 移动端筛选切换按钮 */
  .mobile-filter-toggle { display: none; }
  .mobile-filter-toggle.active { color: var(--accent-blue); }
  .search-input {
    flex: 0 0 auto;
    width: min(44vw, 150px);
    min-width: 0;
  }

  /* 移动端筛选下拉菜单：相对工具栏定位 */
  .mobile-filter-dropdown {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    z-index: 100;
    pointer-events: none;
  }
  .filter-backdrop {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    pointer-events: auto;
  }
  .filter-dropdown-menu {
    position: relative;
    pointer-events: auto;
    background: var(--bg-primary);
    border-bottom: 1px solid var(--border-color);
    box-shadow: var(--ui-shadow-sm);
    max-height: 60vh;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
  /* 紧凑下拉面板：右侧对齐，固定宽度，圆角卡片（用于邮件列表，只有4个选项） */
  .filter-dropdown-compact {
    position: absolute;
    right: 12px;
    top: 8px;
    min-width: 140px;
    max-height: none;
    border: 1px solid var(--border-color);
    border-radius: 10px;
    box-shadow: var(--ui-shadow-md);
    padding: 4px 0;
    overflow-y: auto;
    max-height: 50vh;
  }
  .filter-dropdown-item {
    display: block;
    width: 100%;
    padding: 10px 16px;
    font-size: 14px;
    text-align: left;
    background: none;
    border: none;
    color: var(--text-primary);
    cursor: pointer;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .filter-dropdown-item:active { background: var(--bg-secondary); }
  .filter-dropdown-item.active { color: var(--accent-blue); font-weight: 600; }

  /* 下拉菜单动画 */
  .filter-dropdown-enter-active { animation: dropIn 0.15s ease; }
  .filter-dropdown-leave-active { animation: dropOut 0.1s ease; }
  @keyframes dropIn { from { opacity: 0; transform: translateY(-4px) scale(0.96); } to { opacity: 1; transform: translateY(0) scale(1); } }
  @keyframes dropOut { from { opacity: 1; transform: translateY(0) scale(1); } to { opacity: 0; transform: translateY(-4px) scale(0.96); } }

  .account-tabs {
    padding: var(--space-2) var(--space-3);
    overflow-x: auto;
  }

  .list-items {
    padding-bottom: 0;
  }

  .mail-item {
    padding: 10px 12px;
    min-height: 60px;
    min-width: 560px;
  }

  .mail-avatar {
    width: 30px;
    height: 30px;
    font-size: 12px;
  }

  .mail-from,
  .mail-subject {
    font-size: 12px;
  }

  .mail-status-tag {
    width: 36px;
    font-size: 9px;
    margin-left: 6px;
  }

  .mail-date {
    width: 54px;
    font-size: 10px;
    margin-left: 6px;
  }

  .pagination {
    justify-content: space-between;
    gap: 6px;
    padding: 10px 12px calc(10px + env(safe-area-inset-bottom, 0px));
    box-shadow: var(--ui-shadow-sm);
    flex-wrap: nowrap;
  }

  .page-btn {
    min-width: 44px;
    height: 36px;
    border-radius: 10px;
    background: var(--bg-secondary);
  }

  .page-btn.page-nav {
    min-width: 92px;
    padding: 0 10px;
    justify-content: center;
  }

  .page-ellipsis {
    width: 18px;
  }

  .page-mobile-summary {
    flex: 1;
    min-width: 0;
  }

  /* iOS风格文件夹选择按钮 */
  .folder-picker {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 5px 12px;
    border: none;
    border-radius: var(--radius-md);
    background: var(--ui-fill-selected);
    color: var(--accent-blue);
    font-size: var(--text-sm);
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .folder-picker:active {
    background: var(--ui-accent-soft);
  }
  .picker-label {
    max-width: 120px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* iOS风格底部弹出层 */
  .sheet-backdrop {
    position: fixed;
    inset: 0;
    background: var(--ui-scrim);
    z-index: 1000;
    display: flex;
    align-items: flex-end;
    justify-content: center;
  }
  .sheet-content {
    width: 100%;
    max-width: 420px;
    max-height: 60vh;
    background: var(--bg-primary);
    border-radius: 14px 14px 0 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .sheet-handle {
    width: 36px;
    height: 5px;
    border-radius: 3px;
    background: var(--border-color-strong);
    margin: 8px auto 4px;
  }
  .sheet-title {
    padding: 8px 20px 12px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
    text-align: center;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .sheet-list {
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    padding-bottom: env(safe-area-inset-bottom, 20px);
  }
  .sheet-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 13px 20px;
    border: none;
    background: var(--bg-primary);
    font-size: 17px;
    color: var(--text-primary);
    text-align: left;
    cursor: pointer;
    transition: background 0.15s;
  }
  .sheet-item:active {
    background: var(--bg-hover);
  }
  .sheet-item.active {
    color: var(--accent-blue);
    font-weight: 500;
  }
  .sheet-item + .sheet-item {
    border-top: 0.5px solid var(--border-color);
  }
  .sheet-folder-name {
    flex: 1;
  }
  .sheet-folder-count {
    font-size: 15px;
    color: var(--text-secondary);
    margin-right: 8px;
  }

  /* 弹出层动画 */
  .sheet-enter-active, .sheet-leave-active {
    transition: opacity 0.3s ease;
  }
  .sheet-enter-from, .sheet-leave-to {
    opacity: 0;
  }
  .sheet-enter-from .sheet-content, .sheet-leave-to .sheet-content {
    transform: translateY(100%);
  }
  .sheet-enter-active .sheet-content, .sheet-leave-active .sheet-content {
    transition: transform 0.3s ease;
  }

  .detail-header {
    padding: var(--space-3) var(--space-4) var(--space-2);
  }

  .detail-body,
  .detail-content-wrap,
  .detail-content {
    width: 100%;
    max-width: 100%;
    min-width: 0;
    box-sizing: border-box;
  }

  .detail-body {
    overflow-x: hidden;
  }

  .detail-content-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  .detail-toolbar {
    gap: 8px;
    align-items: flex-start;
  }

  .detail-actions {
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .btn-action span,
  .btn-back span {
    display: none;
  }

  .btn-action,
  .btn-back {
    min-width: 36px;
    justify-content: center;
    padding: 6px 8px;
  }

  .detail-subject {
    font-size: var(--text-lg);
  }

  .detail-content {
    padding: var(--space-3) var(--space-4);
    overflow-wrap: anywhere;
    word-break: break-word;
  }
}

.compose-entry-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: none;
  border-radius: 8px;
  background: var(--color-accent);
  color: var(--ui-text-inverse);
  font-size: var(--text-xs);
  font-weight: var(--font-medium);
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  flex: 0 0 auto;
}

.compose-entry-btn:hover {
  opacity: 0.9;
}

/* Apple Mail 风格邮件工作区 */
.mail-shell,
.mail-shell.detail {
  grid-template-columns: var(--mail-folder-pane) minmax(320px, var(--mail-list-pane)) minmax(0, 1fr);
  gap: 0;
  border: 1px solid var(--border-color);
  border-radius: var(--ui-radius-lg);
  background: var(--bg-card);
  box-shadow: var(--ui-shadow-xs);
  overflow: hidden;
}

.folder-sidebar {
  display: flex;
  flex-direction: column;
  padding: 0;
  border-right: 1px solid var(--border-color);
  border-radius: 0;
  background: var(--ui-surface-2);
  box-shadow: none;
  overflow: hidden;
}

.folder-sidebar-header {
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 650;
}

.folder-sidebar-header span:last-child {
  color: var(--text-tertiary);
  font-size: 10px;
  font-weight: 500;
}

.account-switcher {
  padding: 10px 10px 8px;
  border-bottom: 1px solid var(--border-color);
}

.account-switcher-row {
  position: relative;
  display: flex;
  align-items: center;
}

.account-switcher-item {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 32px 8px 9px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.account-switcher-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}

.account-switcher-item.active {
  background: var(--bg-active);
  color: var(--color-accent);
}

.account-switcher-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.account-switcher-copy strong,
.account-switcher-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-switcher-copy strong {
  font-size: 12px;
  font-weight: 600;
}

.account-switcher-copy small {
  color: var(--text-tertiary);
  font-size: 10px;
}

.account-reauth {
  position: absolute;
  right: 7px;
  z-index: 1;
}

.folder-nav-list {
  flex: 1;
  min-height: 0;
  padding: 8px 10px 12px;
  overflow-y: auto;
}

.folder-nav-item {
  min-height: 38px;
  justify-content: flex-start;
  gap: 9px;
  padding: 8px 10px;
  border-radius: 8px;
}

.folder-nav-item > svg {
  flex: 0 0 auto;
}

.folder-nav-name {
  flex: 1;
}

.folder-nav-item.active {
  background: var(--bg-active);
  color: var(--color-accent);
}

.mail-list {
  min-width: 0;
  border-right: 1px solid var(--border-color);
  border-radius: 0;
  background: var(--bg-card);
  box-shadow: none;
}

.list-toolbar {
  min-height: 48px;
  padding: 7px 12px;
  gap: 10px;
  background: color-mix(in srgb, var(--bg-card) 92%, var(--bg-tertiary));
}

.toolbar-left,
.toolbar-right {
  gap: 6px;
}

.search-input {
  width: 250px;
  height: 34px;
  border-color: var(--border-color-strong);
  background: var(--bg-secondary);
}

.filter-btn {
  min-height: 28px;
  padding: 3px 9px;
  border-radius: 7px;
}

.filter-btn.active {
  background: var(--bg-active);
}

.btn-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.compose-entry-btn {
  height: 34px;
  padding: 0 13px;
  border-radius: 9px;
  box-shadow: var(--ui-shadow-xs);
}

.list-items {
  background: transparent;
}

.mail-item,
.mail-item:not(.unread) {
  position: relative;
  min-height: var(--list-row-height);
  padding: 8px 12px 8px 17px;
  background: transparent;
}

.mail-item::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 50%;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: transparent;
  transform: translateY(-50%);
}

.mail-item.unread::before {
  background: var(--color-accent);
}

.mail-item:hover,
.mail-item:not(.unread):hover {
  background: var(--bg-hover);
}

.mail-item.selected {
  background: var(--bg-active);
}

.mail-item:not(.unread) .mail-from,
.mail-item:not(.unread) .mail-subject,
.mail-item:not(.unread) .mail-date {
  color: var(--text-secondary);
}

.mail-item:not(.unread) .mail-avatar {
  opacity: 0.82;
}

.mail-sender {
  width: 190px;
  gap: 11px;
  padding-right: 16px;
}

.mail-avatar {
  width: 32px;
  height: 32px;
}

.mail-status-icon.unread-icon {
  color: var(--color-accent);
}

.mail-status-icon.read-icon {
  display: none;
}

.mail-status-tag {
  width: 40px;
  border-radius: 5px;
}

.mail-status-tag.unread {
  background: var(--color-accent-lighter);
  color: var(--color-accent);
}

.mail-status-tag.read {
  background: transparent;
  color: var(--text-tertiary);
}

.mail-date {
  width: 58px;
  padding-left: 8px;
}

.pagination {
  min-height: var(--toolbar-height);
  background: var(--bg-card);
}

.mail-detail {
  min-width: 0;
  border-radius: 0;
  background: var(--bg-card);
  box-shadow: none;
}

.mail-detail-empty {
  display: grid;
  place-items: center;
  padding: var(--page-gutter);
}

.mail-detail-empty :deep(.ui-empty-state) {
  max-width: 320px;
}

.btn-back {
  display: none;
}

@media (max-width: 1180px) and (min-width: 961px) {
  .mail-shell,
  .mail-shell.detail {
    grid-template-columns: minmax(320px, var(--mail-list-pane)) minmax(0, 1fr);
  }

  .folder-sidebar {
    display: none;
  }

  .mail-sender {
    width: 142px;
  }

  .search-input {
    width: 180px;
  }

  .list-count,
  .toolbar-divider {
    display: none;
  }
}

@media (max-width: 960px) {
  .mail-shell,
  .mail-shell.detail {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }

  .folder-sidebar {
    display: none;
  }

  .mail-list {
    border-right: 0;
  }

  .btn-back {
    display: inline-flex;
  }
}

@media (max-width: 768px) {
  .mail-view,
  .mail-shell,
  .mail-list,
  .list-items {
    width: 100%;
    max-width: 100%;
    min-width: 0;
  }

  .mail-view {
    height: 100%;
    overflow: hidden;
  }

  .mail-shell {
    height: 100%;
  }

  .mail-list {
    min-height: 0;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }

  .list-toolbar {
    flex-wrap: wrap;
    gap: 7px;
    padding: 8px;
    overflow: visible;
  }

  .toolbar-left {
    width: 100%;
    flex: 0 0 100%;
    gap: 6px;
    overflow: hidden;
  }

  .toolbar-left .filter-btn,
  .toolbar-left .toolbar-divider {
    display: none;
  }

  .toolbar-right {
    width: 100%;
    flex: 0 0 100%;
    display: flex;
    gap: 5px;
  }

  .search-input {
    width: auto;
    min-width: 0;
    flex: 1 1 auto;
  }

  .mobile-filter-toggle {
    display: inline-flex;
  }

  .sync-status {
    display: none;
  }

  .folder-picker {
    min-width: 0;
    max-width: 42vw;
    padding-inline: 10px;
  }

  .picker-label {
    max-width: 100%;
  }

  .list-items {
    overflow-x: hidden;
  }

  .mail-item,
  .mail-item:not(.unread) {
    width: 100%;
    min-width: 0;
    min-height: 72px;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    grid-template-areas:
      "select sender date"
      "select info info";
    column-gap: 8px;
    row-gap: 5px;
    padding: 10px 12px 10px 17px;
    overflow: hidden;
  }

  .check-circle {
    grid-area: select;
    align-self: center;
    margin-right: 0;
  }

  .mail-sender {
    grid-area: sender;
    width: auto;
    min-width: 0;
    gap: 9px;
    padding-right: 0;
  }

  .mail-info {
    grid-area: info;
    width: 100%;
    min-width: 0;
    padding-left: 39px;
  }

  .mail-main-row {
    width: 100%;
    min-width: 0;
  }

  .mail-avatar {
    width: 30px;
    height: 30px;
  }

  .mail-from {
    font-size: 13px;
  }

  .mail-subject {
    width: 100%;
    min-width: 0;
    font-size: 12px;
    color: var(--text-secondary);
  }

  .mail-status-icon,
  .mail-status-tag {
    display: none;
  }

  .mail-date {
    grid-area: date;
    width: auto;
    align-self: center;
    margin: 0;
    padding: 0;
    font-size: 10px;
  }

  .pagination {
    width: 100%;
    min-width: 0;
    padding-inline: 8px;
  }

  .page-btn.page-nav {
    min-width: 78px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .folder-nav-item,
  .account-switcher-item,
  .mail-item,
  .btn-icon,
  .compose-entry-btn {
    transition: none;
  }

  .refresh-button.is-refreshing svg {
    animation: none;
  }
}

/* 骨架屏：正文加载中的占位动画 */
.body-skeleton {
  padding: var(--space-5);
}

.skeleton-line {
  height: 14px;
  background: linear-gradient(90deg, var(--bg-tertiary) 25%, var(--bg-hover) 50%, var(--bg-tertiary) 75%);
  background-size: 200% 100%;
  animation: skeleton-loading 1.5s infinite;
  border-radius: 4px;
  margin-bottom: 12px;
}

@keyframes skeleton-loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
