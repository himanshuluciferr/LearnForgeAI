/** Shapes shown while something is on its way.
 *
 *  A course is a few hundred kilobytes read from another continent, so these are on screen
 *  for seconds rather than a flicker. A blank "Loading…" for that long reads as broken.
 */

export function Shimmer({ width, height = "1rem" }: { width: string; height?: string }) {
  return <span className="shimmer" style={{ width, height }} aria-hidden="true" />;
}

export function LibrarySkeleton() {
  return (
    <div className="shelf" aria-busy="true" aria-label="Loading your courses">
      {[0, 1, 2].map((slot) => (
        <div key={slot} className="book skeleton">
          <Shimmer width="85%" height="1.1rem" />
          <Shimmer width="60%" height="1.1rem" />
          <Shimmer width="40%" />
        </div>
      ))}
    </div>
  );
}

export function CourseSkeleton() {
  return (
    <div className="page" aria-busy="true" aria-label="Loading the course">
      <header className="bar">
        <span className="muted">← Library</span>
      </header>
      <div className="stack">
        <Shimmer width="65%" height="2rem" />
        <Shimmer width="90%" />
      </div>
      <div className="contents">
        {[0, 1, 2, 3, 4].map((slot) => (
          <div key={slot} className="chapter-row skeleton">
            <Shimmer width="1.5rem" />
            <Shimmer width={`${45 + slot * 8}%`} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function QuizSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the quiz">
      {[0, 1].map((slot) => (
        <fieldset key={slot} className="question skeleton">
          <Shimmer width="80%" height="1.1rem" />
          <Shimmer width="55%" />
          <Shimmer width="62%" />
          <Shimmer width="48%" />
        </fieldset>
      ))}
    </div>
  );
}
