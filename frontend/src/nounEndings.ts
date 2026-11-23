/**
 * The endings of Polish nouns, kept here so that the user does not have to type the
 * same predictable endings each time. Each set has the endings of every case, in the
 * singular and the plural together, as they are written after the stem (a stem that
 * changes, "Polska" and "Polsce", "Kraków" and "Krakowa", is another core). The
 * endings of the declension classes of a gender (hard and soft stems, the ones after
 * k and g) are put together, so a set has some that a noun does not use. The zero
 * ending (the nominative of a masculine, the genitive plural of a feminine) is not in
 * them: it would be the core alone, which is in every word that has the core.
 */
export interface EndingSet {
  id: 'masculine' | 'feminine' | 'neuter' | 'adjectival'
  label: string
  /** Said in the tooltip of its button. */
  description: string
  endings: string[]
}

export const ENDING_SETS: EndingSet[] = [
  {
    id: 'masculine',
    label: 'Masculine',
    description:
      'The endings of masculine nouns in every case, singular and plural: a, u, owi, em, iem, e, ie, y, i, owie, ów, om, ami, mi, ach',
    endings: [
      'a', 'u', 'owi', 'em', 'iem', 'e', 'ie', 'y', 'i', 'owie', 'ów', 'om', 'ami', 'mi', 'ach',
    ],
  },
  {
    id: 'feminine',
    label: 'Feminine',
    description:
      'The endings of feminine nouns in every case, singular and plural: a, y, i, ie, e, ę, ą, o, om, ami, ach',
    endings: ['a', 'y', 'i', 'ie', 'e', 'ę', 'ą', 'o', 'om', 'ami', 'ach'],
  },
  {
    id: 'neuter',
    label: 'Neuter',
    description:
      'The endings of neuter nouns in every case, singular and plural: o, e, ę, um, a, u, em, iem, ie, i, y, ów, om, ami, ach',
    endings: [
      'o', 'e', 'ę', 'um', 'a', 'u', 'em', 'iem', 'ie', 'i', 'y', 'ów', 'om', 'ami', 'ach',
    ],
  },
  {
    id: 'adjectival',
    label: 'Adjectival',
    description:
      'The endings of adjectives and of surnames in -ski, -cki, -dzki, in every case: i, iego, iemu, im, ich, imi, a, iej, ą',
    endings: ['i', 'iego', 'iemu', 'im', 'ich', 'imi', 'a', 'iej', 'ą'],
  },
]
