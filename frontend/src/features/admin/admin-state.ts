export function canAccessAdminRoute(role: string): boolean {
  return role === 'admin';
}

export function exactConfirmation(expected: string, typed: string): boolean {
  return expected.length > 0 && expected === typed;
}
