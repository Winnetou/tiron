import { afterEach, describe, expect, it } from 'vitest'
import { caretInText, selectionInText } from './selection'

let box: HTMLPreElement | null = null

function show(text: string): HTMLPreElement {
  box = document.createElement('pre')
  box.textContent = text
  document.body.append(box)
  return box
}

function rangeIn(
  container: HTMLElement,
  from: number,
  to: number,
  node: Node = container.firstChild!,
): Range {
  const range = document.createRange()
  range.setStart(node, from)
  range.setEnd(node, to)
  return range
}

afterEach(() => {
  box?.remove()
  box = null
})

describe('selectionInText', () => {
  it('gives the offsets and the text of the selection', () => {
    const text = 'Ala ma kota. Jan Kowalski mieszka tu.'
    const pre = show(text)

    expect(selectionInText(pre, rangeIn(pre, 13, 25), text)).toEqual({
      start: 13,
      end: 25,
      text: 'Jan Kowalski',
    })
  })

  it('works over line breaks', () => {
    const text = 'one\ntwo\nthree'
    const pre = show(text)

    expect(selectionInText(pre, rangeIn(pre, 4, 13), text)).toEqual({
      start: 4,
      end: 13,
      text: 'two\nthree',
    })
  })

  it('leaves out the spaces and line breaks at the ends', () => {
    const text = 'a\n  Jan Kowalski \nb'
    const pre = show(text)

    const found = selectionInText(pre, rangeIn(pre, 1, text.length - 1), text)

    expect(found).toEqual({ start: 4, end: 16, text: 'Jan Kowalski' })
    expect(text.slice(found!.start, found!.end)).toBe('Jan Kowalski')
  })

  it('counts code points, as the backend does, after emoji', () => {
    const text = '😀😀 Jan 😀'
    const pre = show(text)
    const from = text.indexOf('Jan') // a position in the JavaScript string

    const found = selectionInText(pre, rangeIn(pre, from, from + 3), text)

    // each emoji is one character for the backend, but two for JavaScript
    expect(found).toEqual({ start: 3, end: 6, text: 'Jan' })
  })

  it('counts code points when the selection contains an emoji', () => {
    const text = 'a 😀b c'
    const pre = show(text)

    const found = selectionInText(pre, rangeIn(pre, 2, 5), text)

    expect(found).toEqual({ start: 2, end: 4, text: '😀b' })
  })

  it('finds a selection that starts and ends in different nodes', () => {
    const pre = document.createElement('pre')
    pre.innerHTML = 'Ala <b>ma</b> kota'
    document.body.append(pre)
    box = pre
    const range = document.createRange()
    range.setStart(pre.firstChild!, 2)
    range.setEnd(pre.lastChild!, 3)

    expect(selectionInText(pre, range, 'Ala ma kota')).toEqual({
      start: 2,
      end: 9,
      text: 'a ma ko',
    })
  })

  it('finds a selection whose ends are element boundaries', () => {
    const text = 'Ala ma kota'
    const pre = show(text)
    const range = document.createRange()
    range.selectNodeContents(pre)

    expect(selectionInText(pre, range, text)).toEqual({
      start: 0,
      end: 11,
      text,
    })
  })

  it('is null for a selection of only spaces', () => {
    const text = 'a   b'
    const pre = show(text)

    expect(selectionInText(pre, rangeIn(pre, 1, 4), text)).toBeNull()
  })

  it('is null for an empty selection', () => {
    const text = 'Ala'
    const pre = show(text)

    expect(selectionInText(pre, rangeIn(pre, 1, 1), text)).toBeNull()
  })

  it('is null when the selection is not in the container', () => {
    const text = 'Ala ma kota'
    const pre = show(text)
    const outside = document.createElement('p')
    outside.textContent = 'elsewhere'
    document.body.append(outside)

    expect(selectionInText(pre, rangeIn(outside, 0, 5), text)).toBeNull()
    outside.remove()
  })

  it('is null when the selection starts inside and ends outside', () => {
    const text = 'Ala ma kota'
    const pre = show(text)
    const outside = document.createElement('p')
    outside.textContent = 'elsewhere'
    document.body.append(outside)
    const range = document.createRange()
    range.setStart(pre.firstChild!, 2)
    range.setEnd(outside.firstChild!, 3)

    expect(selectionInText(pre, range, text)).toBeNull()
    outside.remove()
  })

  it('is null when the container no longer shows that text', () => {
    const pre = show('Another text')

    expect(selectionInText(pre, rangeIn(pre, 0, 3), 'Ala ma kota')).toBeNull()
  })

  it('gives the same offsets whichever way the selection was made', () => {
    // dragging backwards: the anchor is after the focus
    const text = 'Ala ma kota'
    const pre = show(text)
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.collapse(pre.firstChild, 7)
    selection.extend(pre.firstChild!, 4)

    // the range is "ma " (4 to 7), and without its trailing space "ma"
    expect(selectionInText(pre, selection.getRangeAt(0), text)).toEqual({
      start: 4,
      end: 6,
      text: 'ma',
    })
    selection.removeAllRanges()
  })

  describe('with a dialog in the container', () => {
    /** "Ala " + <mark>ma<dialog>XYZ</dialog></mark> + " kota" */
    function withDialog() {
      const pre = document.createElement('pre')
      pre.append('Ala ')
      const mark = document.createElement('mark')
      mark.append('ma')
      const dialog = document.createElement('div')
      dialog.setAttribute('role', 'dialog')
      dialog.textContent = 'XYZ'
      mark.append(dialog)
      pre.append(mark, ' kota')
      document.body.append(pre)
      box = pre
      return { pre, dialog }
    }

    it('does not count the text of the dialog', () => {
      const { pre } = withDialog()
      const range = document.createRange()
      range.setStart(pre.lastChild!, 1) // "kota", after the dialog
      range.setEnd(pre.lastChild!, 5)

      expect(selectionInText(pre, range, 'Ala ma kota')).toEqual({
        start: 7,
        end: 11,
        text: 'kota',
      })
    })

    it('finds a selection that ends in the text before the dialog', () => {
      const { pre } = withDialog()
      const range = document.createRange()
      range.setStart(pre.firstChild!, 0)
      range.setEnd(pre.querySelector('mark')!.firstChild!, 2)

      expect(selectionInText(pre, range, 'Ala ma kota')).toEqual({
        start: 0,
        end: 6,
        text: 'Ala ma',
      })
    })

    it('is null for a selection in the dialog', () => {
      const { pre, dialog } = withDialog()
      const range = document.createRange()
      range.setStart(dialog.firstChild!, 0)
      range.setEnd(dialog.firstChild!, 2)

      expect(selectionInText(pre, range, 'Ala ma kota')).toBeNull()
    })
  })
})

describe('caretInText', () => {
  it('is the offset of a point in the text', () => {
    const pre = show('Ala ma kota')

    expect(caretInText(pre, pre.firstChild!, 0, 'Ala ma kota')).toBe(0)
    expect(caretInText(pre, pre.firstChild!, 4, 'Ala ma kota')).toBe(4)
    expect(caretInText(pre, pre.firstChild!, 11, 'Ala ma kota')).toBe(11)
  })

  it('counts code points, not UTF-16 units', () => {
    const pre = show('😀 Ala')

    expect(caretInText(pre, pre.firstChild!, 3, '😀 Ala')).toBe(2)
  })

  it('counts across elements', () => {
    const pre = document.createElement('pre')
    pre.innerHTML = 'Ala <mark>ma</mark> kota'
    document.body.append(pre)
    box = pre

    expect(caretInText(pre, pre.querySelector('mark')!.firstChild!, 1, 'Ala ma kota')).toBe(5)
    expect(caretInText(pre, pre.lastChild!, 1, 'Ala ma kota')).toBe(7)
  })

  it('does not count the text of a dialog', () => {
    const pre = document.createElement('pre')
    pre.innerHTML = 'Ala <mark>ma<div role="dialog">XYZ</div></mark> kota'
    document.body.append(pre)
    box = pre

    expect(caretInText(pre, pre.lastChild!, 1, 'Ala ma kota')).toBe(7)
  })

  it('is null in a dialog, outside the container, or when the text has changed', () => {
    const pre = document.createElement('pre')
    pre.innerHTML = 'Ala <mark>ma<div role="dialog">XYZ</div></mark>'
    document.body.append(pre)
    box = pre
    const outside = document.createElement('p')
    outside.textContent = 'x'
    document.body.append(outside)

    expect(caretInText(pre, pre.querySelector('div')!.firstChild!, 1, 'Ala ma')).toBeNull()
    expect(caretInText(pre, outside.firstChild!, 0, 'Ala ma')).toBeNull()
    expect(caretInText(pre, pre.firstChild!, 0, 'Another')).toBeNull()
    outside.remove()
  })
})
