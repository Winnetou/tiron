import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Loading, PageLoading } from './Loading'

describe('Loading', () => {
  it('is a status with the text, and the small skull before it, only to look at', () => {
    render(<Loading>Looking for the same mistake …</Loading>)

    const status = screen.getByRole('status')

    expect(status).toHaveTextContent('Looking for the same mistake …')
    const image = status.querySelector('img')!
    expect(image).toHaveAttribute('src', '/skull-crossbones-loop.gif')
    expect(image).toHaveAttribute('alt', '')
  })
})

describe('PageLoading', () => {
  it('is the large skull and a status with the text', () => {
    const { container } = render(<PageLoading>Loading the page …</PageLoading>)

    expect(screen.getByRole('status')).toHaveTextContent('Loading the page …')
    expect(container.querySelector('img')).toHaveAttribute('width', '160')
  })
})
