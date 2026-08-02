interface PrintableMailOptions {
  subject: string;
  source: HTMLElement;
  document?: Document;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character] || character);
}

export function buildPrintableMailHtml(options: PrintableMailOptions): string {
  const documentRef = options.document || document;
  const clone = options.source.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('button, [role="button"], [data-remote-image-blocked], .v2-remote-image-control').forEach((node) => node.remove());
  clone.querySelectorAll<HTMLElement>('[style]').forEach((node) => node.removeAttribute('style'));
  clone.querySelectorAll('a').forEach((link) => {
    link.removeAttribute('target');
    link.setAttribute('rel', 'noopener noreferrer');
  });
  const wrapper = documentRef.createElement('div');
  wrapper.appendChild(clone);
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escapeHtml(options.subject)}</title><style>
    @page{size:A4 portrait;margin:12mm}body{font-family:system-ui,sans-serif;color:#161616;background:#fff;line-height:1.65}
    h1{font-size:20px}.mail-print-body{font-size:14px}.mail-print-body img{max-width:100%;height:auto}a{color:#0645ad}
  </style></head><body><h1>${escapeHtml(options.subject || '（无主题）')}</h1><main class="mail-print-body">${wrapper.innerHTML}</main></body></html>`;
}

export async function exportSanitizedMailToPdf(options: PrintableMailOptions): Promise<void> {
  const iframe = document.createElement('iframe');
  iframe.setAttribute('title', '邮件打印预览');
  iframe.style.cssText = 'position:fixed;width:1px;height:1px;inset:auto auto 0 0;opacity:0;pointer-events:none';
  document.body.appendChild(iframe);
  try {
    const frameDocument = iframe.contentDocument;
    if (!frameDocument) throw new Error('无法创建安全打印预览');
    frameDocument.open();
    frameDocument.write(buildPrintableMailHtml(options));
    frameDocument.close();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    iframe.contentWindow?.focus();
    iframe.contentWindow?.print();
  } finally {
    setTimeout(() => iframe.remove(), 1000);
  }
}
