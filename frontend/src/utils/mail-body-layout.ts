const CSS_LENGTH_PATTERN = /^\s*(-?(?:\d+(?:\.\d+)?|\.\d+))(?:[a-z%]+)?\s*$/i;

export function isNegativeCssLength(value: string): boolean {
  const match = String(value || '').match(CSS_LENGTH_PATTERN);
  return Boolean(match && Number(match[1]) < 0);
}
