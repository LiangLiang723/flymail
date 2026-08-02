<template>
  <div class="v2-layout v2-layout--tablet" aria-label="平板邮件布局">
    <aside data-region="navigation" class="v2-layout__navigation"><slot name="navigation" /></aside>
    <main data-region="content" class="v2-layout__content"><slot><router-view /></slot></main>
  </div>
</template>
