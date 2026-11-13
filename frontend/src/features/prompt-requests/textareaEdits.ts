export interface TextSelection {
  readonly start: number
  readonly end: number
}

// The textarea exposes LF and UTF-16 offsets. Keep the underlying string and
// splice just the range edited in that projection.
export function applyTextareaEdit(original: string, next: string, caret: number, selection?: TextSelection): string {
  if (!original.includes('\r')) return next
  const displayed = original.replace(/\r\n?/g, '\n')
  const delta = next.length - displayed.length

  function splice(start: number, end: number) {
    function originalOffset(offset: number) {
      let index = 0
      for (let displayedIndex = 0; displayedIndex < offset; displayedIndex++, index++) {
        if (original[index] === '\r' && original[index + 1] === '\n') index++
      }
      return index
    }
    return original.slice(0, originalOffset(start)) + next.slice(start, end + delta) + original.slice(originalOffset(end))
  }

  function matches(start: number, end: number) {
    return start >= 0 && end >= start && end <= displayed.length && end + delta >= start &&
      displayed.slice(0, start) === next.slice(0, start) &&
      displayed.slice(end) === next.slice(end + delta)
  }

  if (selection) {
    // Prefer the actual selection over a text diff when repeated lines can
    // align multiple ways, including replacement with identical visible text.
    if (selection.start === selection.end && delta < 0 && matches(caret, caret - delta)) {
      return splice(caret, caret - delta)
    }
    if (matches(selection.start, selection.end)) return splice(selection.start, selection.end)
  }
  if (displayed === next) return original
  let start = 0
  while (start < displayed.length && start < next.length && displayed[start] === next[start]) start++
  let end = displayed.length
  while (end > start && end + delta > start && displayed[end - 1] === next[end + delta - 1]) end--
  return splice(start, end)
}
