import type { NodeViewRendererProps } from '@tiptap/core';
import { NodeSelection } from '@tiptap/pm/state';
import { clampImageWidth, imageWidthFromPercent, parseImageWidth } from './editor-image-size';

const QUICK_IMAGE_SIZES = [25, 50, 75, 100] as const;

function getNodePosition(getPos: NodeViewRendererProps['getPos']): number | null {
  if (typeof getPos !== 'function') return null;
  const position = getPos();
  return typeof position === 'number' ? position : null;
}

export function createResizableImageNodeView({
  node: initialNode,
  editor,
  getPos,
}: NodeViewRendererProps) {
  let node = initialNode;
  let dragPointerId: number | null = null;
  let dragStartX = 0;
  let dragStartWidth = 0;
  let dragPreviewWidth = 0;

  const dom = document.createElement('div');
  const image = document.createElement('img');
  const toolbar = document.createElement('div');
  const handle = document.createElement('button');

  dom.className = 'resizable-image-node';
  dom.contentEditable = 'false';

  image.draggable = false;
  image.className = 'resizable-image-element';

  toolbar.className = 'image-size-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', '图片大小');

  handle.type = 'button';
  handle.className = 'image-resize-handle';
  handle.setAttribute('aria-label', '调整图片大小');
  handle.title = '拖动调整图片大小';

  function editorWidth(): number {
    return Math.max(1, Math.round(editor.view.dom.clientWidth || dom.parentElement?.clientWidth || image.clientWidth || 1));
  }

  function syncImageAttributes() {
    image.src = String(node.attrs.src || '');
    image.alt = String(node.attrs.alt || '');
    image.title = String(node.attrs.title || '');
    const width = parseImageWidth(node.attrs.width);
    if (width) {
      const safeWidth = clampImageWidth(width, editorWidth());
      image.width = safeWidth;
      image.style.width = `${safeWidth}px`;
    } else {
      image.removeAttribute('width');
      image.style.removeProperty('width');
    }
  }

  function selectCurrentNode() {
    const position = getNodePosition(getPos);
    if (position === null) return;
    editor.view.dispatch(editor.state.tr.setSelection(NodeSelection.create(editor.state.doc, position)));
    editor.view.focus();
  }

  function updateWidth(width: number) {
    const position = getNodePosition(getPos);
    if (position === null) return;
    const safeWidth = clampImageWidth(width, editorWidth());
    let transaction = editor.state.tr
      .setNodeMarkup(position, undefined, { ...node.attrs, width: safeWidth });
    transaction = transaction.setSelection(NodeSelection.create(transaction.doc, position));
    editor.view.dispatch(transaction);
    editor.view.focus();
  }

  function stopDragging() {
    document.removeEventListener('pointermove', handlePointerMove);
    document.removeEventListener('pointerup', handlePointerUp);
    document.removeEventListener('pointercancel', handlePointerUp);
    dragPointerId = null;
  }

  function handlePointerMove(event: PointerEvent) {
    if (dragPointerId !== event.pointerId) return;
    dragPreviewWidth = clampImageWidth(
      dragStartWidth + event.clientX - dragStartX,
      editorWidth(),
    );
    image.width = dragPreviewWidth;
    image.style.width = `${dragPreviewWidth}px`;
  }

  function handlePointerUp(event: PointerEvent) {
    if (dragPointerId !== event.pointerId) return;
    const finalWidth = dragPreviewWidth || dragStartWidth;
    stopDragging();
    updateWidth(finalWidth);
  }

  function handleResizeStart(event: PointerEvent) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    selectCurrentNode();
    dragPointerId = event.pointerId;
    dragStartX = event.clientX;
    dragStartWidth = image.getBoundingClientRect().width
      || parseImageWidth(node.attrs.width)
      || image.naturalWidth
      || 80;
    dragPreviewWidth = clampImageWidth(dragStartWidth, editorWidth());
    document.addEventListener('pointermove', handlePointerMove);
    document.addEventListener('pointerup', handlePointerUp);
    document.addEventListener('pointercancel', handlePointerUp);
  }

  function handleImagePointerDown(event: PointerEvent) {
    if (event.button !== 0) return;
    event.preventDefault();
    selectCurrentNode();
  }

  for (const percent of QUICK_IMAGE_SIZES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `${percent}%`;
    button.setAttribute('aria-label', `图片宽度 ${percent}%`);
    button.addEventListener('mousedown', (event) => event.preventDefault());
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      updateWidth(imageWidthFromPercent(editorWidth(), percent));
    });
    toolbar.append(button);
  }

  image.addEventListener('pointerdown', handleImagePointerDown);
  handle.addEventListener('pointerdown', handleResizeStart);
  dom.append(image, toolbar, handle);
  syncImageAttributes();

  return {
    dom,
    update(updatedNode: typeof initialNode) {
      if (updatedNode.type !== node.type) return false;
      node = updatedNode;
      syncImageAttributes();
      return true;
    },
    selectNode() {
      dom.classList.add('resizable-image-node--selected');
    },
    deselectNode() {
      dom.classList.remove('resizable-image-node--selected');
    },
    stopEvent(event: Event) {
      const target = event.target;
      return target instanceof globalThis.Node
        && (target === handle || toolbar.contains(target));
    },
    ignoreMutation() {
      return true;
    },
    destroy() {
      stopDragging();
      image.removeEventListener('pointerdown', handleImagePointerDown);
      handle.removeEventListener('pointerdown', handleResizeStart);
    },
  };
}
