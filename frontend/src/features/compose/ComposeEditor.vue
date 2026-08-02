<script setup lang="ts">
import type { Editor } from '@tiptap/vue-3';
import { markRaw, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';

const props = defineProps<{ modelValue: string; textValue?: string }>();
const emit = defineEmits<{
  'update:modelValue': [html: string];
  'update:textValue': [text: string];
}>();
const editor = shallowRef<Editor | null>(null);
const editorContent = shallowRef<unknown>(null);
const loading = ref(true);
const error = ref('');
const fallback = ref(props.textValue || '');

async function loadEditor() {
  loading.value = true;
  error.value = '';
  try {
    const [{ Editor, EditorContent }, starterKitModule] = await Promise.all([
      import('@tiptap/vue-3'),
      import('@tiptap/starter-kit'),
    ]);
    editorContent.value = markRaw(EditorContent);
    editor.value = new Editor({
      extensions: [starterKitModule.default],
      content: props.modelValue || '',
      editorProps: { attributes: { class: 'v2-compose-editor__surface', 'aria-label': '邮件正文编辑器' } },
      onUpdate: ({ editor: instance }) => {
        emit('update:modelValue', instance.getHTML());
        emit('update:textValue', instance.getText());
      },
    });
  } catch (value: unknown) {
    error.value = value instanceof Error ? value.message : '编辑器加载失败';
  } finally {
    loading.value = false;
  }
}

watch(() => props.modelValue, (value) => {
  if (editor.value) editor.value.commands.setContent(value || '', { emitUpdate: false });
});
watch(fallback, (value) => {
  if (!editor.value) {
    emit('update:textValue', value);
    emit('update:modelValue', `<p>${value.replace(/[&<>]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[character] || character).replace(/\n/g, '<br>')}</p>`);
  }
});

onMounted(() => { void loadEditor(); });
onBeforeUnmount(() => editor.value?.destroy());
</script>

<template>
  <section class="v2-compose-editor">
    <p v-if="loading" role="status">正在加载富文本编辑器…</p>
    <div v-else-if="error" class="v2-editor-fallback">
      <p class="v2-error" role="alert">{{ error }}。服务器草稿不会丢失。</p>
      <button type="button" @click="loadEditor">重试编辑器</button>
      <textarea v-model="fallback" rows="14" aria-label="纯文本正文备用编辑器" />
    </div>
    <component :is="editorContent" v-else-if="editor && editorContent" :editor="editor" />
  </section>
</template>
