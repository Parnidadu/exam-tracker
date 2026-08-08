import { useEffect } from 'react'

export interface QueueKeyHandlers {
  onNext: () => void
  onPrev: () => void
  onPickValue: (index: number) => void
  onFocusEvidence: () => void
  onSubmit: () => void
  onToggleHelp: () => void
}

function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable
}

/**
 * Document-level shortcuts for the verifier queue.
 *
 * While the caret is in a text field only Enter (submit) and Escape (blur)
 * are intercepted - otherwise typing a URL containing "e" or a digit would
 * fire navigation commands instead of entering text.
 */
export function useQueueKeys(handlers: QueueKeyHandlers, enabled = true): void {
  useEffect(() => {
    if (!enabled) return

    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return

      const typing = isTextEntry(event.target)

      if (event.key === 'Enter') {
        event.preventDefault()
        handlers.onSubmit()
        return
      }

      if (typing) {
        if (event.key === 'Escape' && event.target instanceof HTMLElement) {
          event.target.blur()
        }
        return
      }

      switch (event.key) {
        case 'j':
        case 'ArrowDown':
          event.preventDefault()
          handlers.onNext()
          break
        case 'k':
        case 'ArrowUp':
          event.preventDefault()
          handlers.onPrev()
          break
        case '1':
        case '2':
        case '3':
          event.preventDefault()
          handlers.onPickValue(Number(event.key) - 1)
          break
        case 'e':
          event.preventDefault()
          handlers.onFocusEvidence()
          break
        case '?':
          event.preventDefault()
          handlers.onToggleHelp()
          break
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [handlers, enabled])
}
