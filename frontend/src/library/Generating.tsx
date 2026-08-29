import { api } from "../api/client";
import { useJobProgress } from "../api/useJobProgress";
import type { JobProgress } from "../api/types";

const STEP_NAMES: Record<string, string> = {
  requirement: "Reading your request",
  "subject-analysis": "Working out the subject",
  research: "Reading the sources",
  curriculum: "Planning the chapters",
  chapter: "Writing the chapters",
  review: "Checking the writing",
  practice: "Making practice tasks",
  project: "Designing projects",
  quiz: "Setting the quizzes",
  publisher: "Binding the book",
};

interface Props {
  jobId: string;
  onFinished: (courseId: string | null) => void;
  onCancel: () => void;
}

export function Generating({ jobId, onFinished, onCancel }: Props) {
  const { progress, error } = useJobProgress(jobId);

  if (error) return <p className="error">{error}</p>;
  if (!progress) return <p className="muted">Starting…</p>;

  if (progress.status === "completed") {
    return (
      <section className="card">
        <p>Your course is ready.</p>
        <button onClick={() => onFinished(progress.course_id)}>Open it</button>
      </section>
    );
  }

  if (progress.status === "failed" || progress.status === "rejected") {
    return (
      <section className="card">
        <p className="error">{progress.error ?? progress.detail ?? "That did not work."}</p>
        <button className="link" onClick={onCancel}>
          Try something else
        </button>
      </section>
    );
  }

  if (progress.status === "needs-choice") return <Choice progress={progress} jobId={jobId} />;
  if (progress.status === "needs-confirmation")
    return <Confirm progress={progress} jobId={jobId} />;

  return (
    <section className="card">
      <div className="progress">
        <div className="bar-fill" style={{ width: `${progress.percent}%` }} />
      </div>
      <p>
        <strong>{STEP_NAMES[progress.step ?? ""] ?? "Working"}</strong> — {progress.percent}%
      </p>
      {progress.detail && <p className="muted">{progress.detail}</p>}
      <p className="hint">This takes a few minutes. You can leave the page open.</p>
    </section>
  );
}

function Choice({ progress, jobId }: { progress: JobProgress; jobId: string }) {
  return (
    <section className="card">
      <p>{progress.detail ?? "Which one did you mean?"}</p>
      <div className="row wrap">
        {progress.options.map((option) => (
          <button key={option} onClick={() => void api.confirm(jobId, option)}>
            {option}
          </button>
        ))}
      </div>
    </section>
  );
}

function Confirm({ progress, jobId }: { progress: JobProgress; jobId: string }) {
  return (
    <section className="card">
      <h3>{progress.subject_name}</h3>
      <p>{progress.subject_description}</p>
      {progress.subject_sources.length > 0 && (
        <ul className="muted small">
          {progress.subject_sources.map((source) => (
            <li key={source}>{source}</li>
          ))}
        </ul>
      )}
      <button onClick={() => void api.confirm(jobId)}>Yes, that is it</button>
    </section>
  );
}
