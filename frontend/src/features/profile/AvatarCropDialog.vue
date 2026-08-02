<script setup lang="ts">
import { ref } from 'vue';

import { cropImageToBlob, normalizeSquareCrop } from '../account-customization/image-crop.ts';

const props = defineProps<{ file: File }>();
const emit = defineEmits<{ cropped: [blob: Blob]; close: [] }>();
const previewUrl = URL.createObjectURL(props.file);
const size = ref(256);
const error = ref('');

async function crop() {
  try {
    const bitmap = await createImageBitmap(props.file, { imageOrientation: 'from-image' });
    const square = normalizeSquareCrop({ x: 0, y: 0, size: Math.min(bitmap.width, bitmap.height), width: bitmap.width, height: bitmap.height, orientation: 1 });
    bitmap.close();
    emit('cropped', await cropImageToBlob(props.file, square, size.value));
  } catch (value: unknown) { error.value = value instanceof Error ? value.message : '头像处理失败'; }
}
</script>

<template>
  <section class="v2-avatar-crop" role="dialog" aria-modal="true" aria-labelledby="avatar-crop-title">
    <h2 id="avatar-crop-title">裁剪头像</h2>
    <img :src="previewUrl" alt="头像裁剪预览" />
    <label>输出尺寸<select v-model.number="size"><option :value="256">256×256 WebP</option></select></label>
    <p v-if="error" class="v2-error">{{ error }}</p>
    <button type="button" @click="crop">使用此头像</button><button type="button" @click="emit('close')">取消</button>
  </section>
</template>
