import { describe, expect, it } from 'vitest'
import { ENDING_SETS } from './nounEndings'

const set = (id: string) => ENDING_SETS.find((s) => s.id === id)!

describe('the sets of noun endings', () => {
  it('are the three genders and the adjectival one, in that order', () => {
    expect(ENDING_SETS.map((s) => s.id)).toEqual([
      'masculine',
      'feminine',
      'neuter',
      'adjectival',
    ])
    expect(ENDING_SETS.map((s) => s.label)).toEqual([
      'Masculine',
      'Feminine',
      'Neuter',
      'Adjectival',
    ])
  })

  it('have the endings of every case, singular and plural', () => {
    expect(set('masculine').endings).toEqual([
      'a', 'u', 'owi', 'em', 'iem', 'e', 'ie', 'y', 'i', 'owie', 'ów', 'om', 'ami', 'mi', 'ach',
    ])
    expect(set('feminine').endings).toEqual([
      'a', 'y', 'i', 'ie', 'e', 'ę', 'ą', 'o', 'om', 'ami', 'ach',
    ])
    expect(set('neuter').endings).toEqual([
      'o', 'e', 'ę', 'um', 'a', 'u', 'em', 'iem', 'ie', 'i', 'y', 'ów', 'om', 'ami', 'ach',
    ])
    expect(set('adjectival').endings).toEqual([
      'i', 'iego', 'iemu', 'im', 'ich', 'imi', 'a', 'iej', 'ą',
    ])
  })

  it('have no zero ending, and none twice', () => {
    for (const { endings } of ENDING_SETS) {
      expect(endings.every((ending) => ending.trim() !== '')).toBe(true)
      expect(new Set(endings).size).toBe(endings.length)
    }
  })

  it('have the endings of the example of the user in the feminine set', () => {
    expect(set('feminine').endings).toEqual(expect.arrayContaining(['a', 'ę', 'i', 'ami']))
  })

  it('are only letters, within what the backend takes', () => {
    for (const { endings } of ENDING_SETS) {
      expect(endings.length).toBeLessThanOrEqual(40)
      for (const ending of endings) expect(ending).toMatch(/^\p{L}{1,30}$/u)
    }
  })

  it('say what is in them in their descriptions', () => {
    for (const { endings, description } of ENDING_SETS) {
      for (const ending of endings) expect(description).toContain(ending)
    }
  })
})
