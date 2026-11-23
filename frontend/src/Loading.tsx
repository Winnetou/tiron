import type { ReactNode } from 'react'

/** The loop of a skull and crossbones that stands for "wait", in `public/`. */
export const SKULL_LOOP = '/skull-crossbones-loop.gif'

/**
 * The loader of a whole page: the skull loop, large, and the text under it (a
 * `status`). Used when an edition is opened and when the list of the editions comes.
 */
export function PageLoading({ children }: { children: ReactNode }) {
  return (
    <div className="page-loading">
      {/* only to look at: the text says it */}
      <img
        src={SKULL_LOOP}
        alt=""
        width={160}
        height={175}
        className="page-loading-image"
      />
      <p role="status">{children}</p>
    </div>
  )
}

/**
 * A line that tells the user that something is being looked for or loaded, with the
 * small skull loop before the text (the loop is only to look at: the text says it,
 * for a screen reader too). It is a `status`, as the plain text was.
 */
export function Loading({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="loading-text">
      <img
        src={SKULL_LOOP}
        alt=""
        width={22}
        height={24}
        className="loading-text-image"
      />
      {children}
    </p>
  )
}
