import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useRef } from 'react'
import { MAX_FONT_PX, MIN_FONT_PX, fitFontSize, useFitFontSize } from './fitFont'

describe('fitFontSize', () => {
  it('is the size at which the longest line fits, with a little room', () => {
    // 100px gives a line of 2000px; the box has 500px: 25px would fit it exactly
    expect(fitFontSize(500, 2000, 100)).toBe(24.5)
  })

  it('stays between the smallest and the largest size', () => {
    expect(fitFontSize(100, 5000, 100)).toBe(MIN_FONT_PX)
    expect(fitFontSize(900, 500, 100)).toBe(MAX_FONT_PX)
  })

  it('is null when there is nothing to go by', () => {
    expect(fitFontSize(0, 100, 100)).toBeNull()
    expect(fitFontSize(500, 0, 100)).toBeNull()
  })
})

function Box({ text, enabled = true }: { text: string; enabled?: boolean }) {
  const ref = useRef<HTMLPreElement>(null)
  useFitFontSize(ref, text, enabled)
  return <pre ref={ref} data-testid="box">{text}</pre>
}

describe('useFitFontSize', () => {
  afterEach(() => vi.restoreAllMocks())

  /** A layout: the box is 600px wide, and a line is 10px wide per character at 100px. */
  function layout() {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(600)
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (
      this: Element,
    ) {
      const width = (this.textContent ?? '').length * 100
      return { width } as DOMRect
    })
  }

  it('sets the font size from the longest line', () => {
    layout()

    const { getByTestId } = render(<Box text={'ab\nabcdefghijkl'} />)

    // the longest line is 1200px at 100px: 600 * 0.98 / 1200 * 100 = 49px, up to the largest
    expect(getByTestId('box').style.fontSize).toBe(`${MAX_FONT_PX}px`)
  })

  it('shrinks the font when the line is too long for the box', () => {
    layout()
    const long = 'x'.repeat(40) // 4000px at 100px: 600 * 0.98 / 4000 * 100 = 14.7px

    const { getByTestId } = render(<Box text={long} />)

    expect(getByTestId('box').style.fontSize).toBe('14.5px')
  })

  it('does nothing where there is no layout', () => {
    const { getByTestId } = render(<Box text="Ala ma kota" />)

    expect(getByTestId('box').style.fontSize).toBe('')
  })

  it('does nothing when it is switched off', () => {
    layout()

    const { getByTestId } = render(<Box text="Ala" enabled={false} />)

    expect(getByTestId('box').style.fontSize).toBe('')
  })

  it('is done again when the text changes', () => {
    layout()
    const { getByTestId, rerender } = render(<Box text={'x'.repeat(40)} />)
    expect(getByTestId('box').style.fontSize).toBe('14.5px')

    rerender(<Box text={'x'.repeat(50)} />)

    expect(getByTestId('box').style.fontSize).toBe('12px')
  })
})
