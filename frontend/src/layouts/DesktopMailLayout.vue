<template>
  <div class="v2-layout v2-layout--desktop" aria-label="桌面邮件布局">
    <aside data-region="navigation" class="v2-layout__navigation"><slot name="navigation" /></aside>
    <section data-region="thread-list" class="v2-layout__list"><slot name="list"><router-view /></slot></section>
    <main data-region="thread-detail" class="v2-layout__detail"><slot name="detail" /></main>
  </div>
</template>
