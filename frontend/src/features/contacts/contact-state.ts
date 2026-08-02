export interface RecipientSuggestion { address: string; display_name?: string }

export function mergeTypedRecipient(typed: string, suggestions: RecipientSuggestion[]): RecipientSuggestion[] {
  const exact = typed.trim();
  const seen = new Set<string>();
  const output: RecipientSuggestion[] = [];
  if (exact) {
    seen.add(exact.toLowerCase());
    output.push({ address: exact, display_name: '' });
  }
  for (const suggestion of suggestions) {
    const key = suggestion.address.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    output.push(suggestion);
  }
  return output;
}
