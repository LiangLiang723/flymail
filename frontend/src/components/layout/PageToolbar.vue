<template>
  <div class="page-toolbar">
    <div v-if="$slots.start" class="page-toolbar__start">
      <slot name="start" />
    </div>
    <div v-if="$slots.default" class="page-toolbar__main">
      <slot />
    </div>
    <div v-if="$slots.end" class="page-toolbar__end">
      <slot name="end" />
    </div>
  </div>
</template>
