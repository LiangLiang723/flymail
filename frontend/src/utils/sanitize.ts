/** HTML 净化配置，防止 XSS 攻击 */
import DOMPurify from 'dompurify'
import { adaptMailBodyColors } from './mail-body-theme'

// 允许的标签白名单
const ALLOWED_TAGS = [
  'a', 'b', 'br', 'div', 'em',
  // font/bgcolor/face/size 等已废弃标签：为兼容老式邮件客户端的 HTML 邮件而保留
  'font', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 'span', 'strong', 'sub', 'sup',
  'table', 'tbody', 'td', 'th', 'thead', 'tr', 'u', 'ul', 'blockquote', 'cite',
]

// 允许的属性白名单
const ALLOWED_ATTR = [
  'href', 'src', 'alt', 'style', 'class', 'id',
  'target', 'rel',
  'width', 'height', 'color', 'size', 'face',
  'align', 'valign', 'bgcolor', 'colspan', 'rowspan',
]

DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

/** 净化邮件 HTML，防止 XSS 注入（移除 script、事件处理器等危险标签） */
export function sanitizeHtml(html: string | undefined | null): string {
  if (!html) return ''
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  })
}

export function escapeHtml(str: string | undefined | null): string {
  if (!str) return ''
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

export function plainTextToSafeHtml(text: string | undefined | null): string {
  if (!text) return ''
  return escapeHtml(text).replace(/\r\n|\r|\n/g, '<br>')
}

export function renderMailBody(
  bodyHtml: string | undefined | null,
  bodyText: string | undefined | null = '',
): string {
  const cleaned = sanitizeHtml(bodyHtml)
  if (cleaned) return cleaned
  return plainTextToSafeHtml(bodyText)
}

export function renderThemedMailBody(
  bodyHtml: string | undefined | null,
  bodyText: string | undefined | null = '',
): string {
  return adaptMailBodyColors(renderMailBody(bodyHtml, bodyText))
}

export function handleMailLinkClick(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  const link = target?.closest('a')
  if (!link) return
  const href = link.getAttribute('href') || ''
  if (!/^(https?:|mailto:)/i.test(href)) {
    e.preventDefault()
    return
  }
  e.preventDefault()
  window.open(href, '_blank', 'noopener,noreferrer')
}
