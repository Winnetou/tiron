import { useEffect, type RefObject } from 'react'

/** The smallest and the largest font size of the text, in px (1.25rem is 20px). */
export const MIN_FONT_PX = 12
export const MAX_FONT_PX = 30

/** The width that is left empty at the end of the longest line, as a share of the box. */
const MARGIN = 0.02

/**
 * The font size at which the longest line just fits the width of the box:
 * `widest` is the width of that line at `measuredAt` px, `available` the room
 * that the box has for it. It is kept between MIN_FONT_PX and MAX_FONT_PX: below
 * the smallest the lines wrap instead, above the largest a short page would
 * have huge letters. Null if there is nothing to go by.
 */
export function fitFontSize(
  available: number,
  widest: number,
  measuredAt: number,
): number | null {
  if (!(available > 0) || !(widest > 0)) return null
  const size = (available * (1 - MARGIN) * measuredAt) / widest
  return Math.round(Math.min(MAX_FONT_PX, Math.max(MIN_FONT_PX, size)) * 2) / 2
}

const MEASURED_AT = 100

/** The width of the longest line of `text`, set in the face of `box`, at MEASURED_AT px. */
function widestLine(box: HTMLElement, text: string): number {
  const probe = document.createElement('span')
  probe.setAttribute('aria-hidden', 'true')
  const family = getComputedStyle(box).fontFamily
  probe.style.cssText = `position:absolute;visibility:hidden;left:-9999px;top:0;white-space:pre;font-family:${family};font-size:${MEASURED_AT}px;font-variant-ligatures:none`
  document.body.appendChild(probe)
  try {
    let widest = 0
    for (const line of new Set(text.split('\n'))) {
      probe.textContent = line
      widest = Math.max(widest, probe.getBoundingClientRect().width)
    }
    return widest
  } finally {
    probe.remove()
  }
}

/**
 * Sets the font size of the box so that the longest line of its text fits its
 * width, without wrapping: smaller if the text is too wide, bigger if there is
 * room to spare (see fitFontSize). It is done again when the text or the width of
 * the box changes. The size is written on the element itself, as it is a matter of
 * the layout and not of what the page shows; where nothing can be measured (no
 * layout) the font of the style sheet stays.
 */
export function useFitFontSize(
  box: RefObject<HTMLElement | null>,
  text: string,
  enabled = true,
) {
  useEffect(() => {
    const element = box.current
    if (!element || !enabled) return
    const fit = () => {
      const style = getComputedStyle(element)
      const available =
        element.clientWidth -
        (parseFloat(style.paddingLeft) || 0) -
        (parseFloat(style.paddingRight) || 0)
      const size = fitFontSize(available, widestLine(element, text), MEASURED_AT)
      if (size === null) element.style.removeProperty('font-size')
      else element.style.setProperty('font-size', `${size}px`)
    }
    fit()
    if (typeof ResizeObserver === 'undefined') return
    let width = element.clientWidth
    const observer = new ResizeObserver(() => {
      // only a change of the width matters (a new size changes the height)
      if (element.clientWidth !== width) {
        width = element.clientWidth
        fit()
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [box, text, enabled])
}
