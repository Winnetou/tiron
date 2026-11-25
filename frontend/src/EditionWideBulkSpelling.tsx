import { useEffect, useState } from "react";
import {
  getBulkSpellingCheck,
  type SpellingFamily,
  type SpellingGroup,
} from "./api";
import { Loading } from "./Loading";

/** One flagged spelling, and what became of it. */
interface Row {
  /** A number of its own. */
  id: number;
  group: SpellingGroup;
  /** Is it among the spellings that "Accept all" takes? */
  include: boolean;
  state: "waiting" | "working" | "accepted" | "failed";
  error?: string;
}

/** A family of spellings, with its rows. */
interface Family {
  stem: string;
  count: number;
  rows: Row[];
}

type Loaded =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "done"; families: Family[] };

const familiesOf = (found: SpellingFamily[]): Family[] => {
  let id = 0;
  return found.map((family) => ({
    stem: family.stem,
    count: family.count,
    rows: family.spellings.map((group) => ({
      id: id++,
      group,
      include: true,
      state: "waiting" as const,
    })),
  }));
};

interface Props {
  editionId: string;
  /**
   * Accept a spelling as legal in the edition (the same request as the "legal
   * spelling" button of a word). Rejects with the reason if the backend refuses.
   */
  onAccept: (spelling: string) => Promise<void>;
  onClose: () => void;
}

/**
 * The big box to accept, as legal spellings, the spellings of the edition that the
 * spell check flags and that are there often, without correcting anything. It is
 * opened from the cog wheel and asks the backend (`GET .../bulk-spelling-check`) at
 * once: no substring is given. The backend checks all the pages, leaves out the first
 * and the last word of every line, and answers with the flagged spellings that are in
 * the edition more than three times, in families of one word with different endings
 * ("Konwencya", "Konwencyi"), the most common first.
 *
 * It then works as the box of the substring does: each spelling is listed with how
 * many times it is in the edition and its first places, each in its context, with
 * an Accept button that makes it a legal spelling of the edition and a box to take
 * it out of "Accept all". Nothing is accepted until the user accepts, and the text
 * of the pages is not changed.
 */
export function BulkSpellingReview({ editionId, onAccept, onClose }: Props) {
  const [loaded, setLoaded] = useState<Loaded>({ kind: "loading" });
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let current = true;
    getBulkSpellingCheck(editionId)
      .then((found) => {
        if (current) setLoaded({ kind: "done", families: familiesOf(found) });
      })
      .catch((error: unknown) => {
        if (current)
          setLoaded({
            kind: "error",
            message: error instanceof Error ? error.message : "Request failed",
          });
      });
    return () => {
      current = false;
    };
  }, [editionId]);

  function updateRows(change: (row: Row) => Row) {
    setLoaded((now) =>
      now.kind === "done"
        ? {
            kind: "done",
            families: now.families.map((family) => ({
              ...family,
              rows: family.rows.map(change),
            })),
          }
        : now,
    );
  }

  const update = (id: number, change: Partial<Row>) =>
    updateRows((row) => (row.id === id ? { ...row, ...change } : row));

  async function accept(row: Row) {
    update(row.id, { state: "working", error: undefined });
    try {
      await onAccept(row.group.text);
    } catch (error) {
      update(row.id, {
        state: "failed",
        error: error instanceof Error ? error.message : "Request failed",
      });
      return;
    }
    update(row.id, { state: "accepted" });
  }

  const families = loaded.kind === "done" ? loaded.families : [];
  const rows = families.flatMap((family) => family.rows);
  const working =
    rows.some((row) => row.state === "working") || progress !== null;
  const todo = rows.filter((row) => row.include && row.state !== "accepted");

  async function acceptAll() {
    const batch = todo;
    setProgress({ done: 0, total: batch.length });
    let done = 0;
    for (const row of batch) {
      await accept(row);
      setProgress({ done: ++done, total: batch.length });
    }
    setProgress(null);
  }

  const accepted = rows.filter((row) => row.state === "accepted").length;
  const failed = rows.filter((row) => row.state === "failed").length;
  const places = rows.reduce((sum, row) => sum + row.group.count, 0);

  return (
    <div className="review-backdrop">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Bulk spelling check"
        className="review-box review-box-substring review-styled"
      >
        <header className="review-header">
          <h2>Bulk spelling check</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="review-body">
          <p className="review-context">
            Spellings that the spell check flags and that are in the edition
            more than three times, without the first and the last word of each
            line. Accepting a spelling makes it a legal spelling of the edition.
          </p>
          {loaded.kind === "loading" && (
            <Loading>Checking the spelling of the edition …</Loading>
          )}
          {loaded.kind === "error" && <p role="alert">{loaded.message}</p>}
          {loaded.kind === "done" && rows.length === 0 && (
            <p role="status">
              No flagged spelling is in the edition often enough.
            </p>
          )}
          {rows.length > 0 && (
            <>
              <p role="status">
                {rows.length} {rows.length === 1 ? "spelling" : "spellings"},{" "}
                {places} {places === 1 ? "place" : "places"} in the edition
                {accepted > 0 && `; ${accepted} accepted`}
                {failed > 0 && `; ${failed} failed`}
              </p>
              <div className="review-toolbar">
                <button
                  type="button"
                  className="review-approve-all"
                  disabled={working || todo.length === 0}
                  onClick={acceptAll}
                >
                  Accept all ({todo.length})
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() =>
                    updateRows((row) => ({ ...row, include: true }))
                  }
                >
                  Include all
                </button>
                <button
                  type="button"
                  disabled={working}
                  onClick={() =>
                    updateRows((row) => ({ ...row, include: false }))
                  }
                >
                  Include none
                </button>
                {progress && (
                  <span role="status">
                    Accepting {progress.done} of {progress.total} …
                  </span>
                )}
              </div>
              {families.map((family) => (
                <section
                  key={family.stem + family.rows[0].id}
                  className="review-family"
                  aria-label={`Spellings like ${family.stem}`}
                >
                  <h3 className="review-family-title">
                    {family.stem}…{" "}
                    <span className="review-context">×{family.count}</span>
                  </h3>
                  <ul className="review-list">
                    {family.rows.map((row) => {
                      const { group } = row;
                      return (
                        <li key={row.id} className={`review-row ${row.state}`}>
                          <input
                            type="checkbox"
                            aria-label={`Include ${group.text} in Accept all`}
                            checked={row.include}
                            disabled={working || row.state === "accepted"}
                            onChange={(event) =>
                              update(row.id, { include: event.target.checked })
                            }
                          />
                          <span className="review-spelling">
                            <strong>{group.text}</strong>{" "}
                            <span className="review-context">
                              ×{group.count}
                            </span>
                            <ul className="review-trigrams">
                              {group.occurrences.map((place) => (
                                <li key={`${place.page_number}:${place.start}`}>
                                  <span className="review-context">
                                    {place.before}
                                  </span>{" "}
                                  <mark>{group.text}</mark>{" "}
                                  <span className="review-context">
                                    {place.after}
                                  </span>{" "}
                                  <span className="review-page">
                                    (page {place.page_number})
                                  </span>
                                </li>
                              ))}
                              {group.count > group.occurrences.length && (
                                <li className="review-context">
                                  and {group.count - group.occurrences.length}{" "}
                                  more
                                </li>
                              )}
                            </ul>
                          </span>
                          {row.state === "accepted" ? (
                            <span className="review-done">✓ Accepted</span>
                          ) : (
                            <button
                              type="button"
                              aria-label={`Accept ${group.text} as a legal spelling`}
                              disabled={working}
                              onClick={() => accept(row)}
                            >
                              Accept
                            </button>
                          )}
                          {row.state === "failed" && (
                            <span role="alert" className="review-error">
                              {row.error}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
