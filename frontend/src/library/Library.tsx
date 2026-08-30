import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { FINISHED } from "../api/useJobProgress";
import type { CourseSummary } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Generating } from "./Generating";

export function Library() {
  const { learner, signOut } = useAuth();
  const navigate = useNavigate();
  const [courses, setCourses] = useState<CourseSummary[] | null>(null);
  const [prompt, setPrompt] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .courses()
      .then(setCourses)
      .catch((caught) => setError(caught.message));

  useEffect(() => {
    void load();
    // The job id lived only in this component, so a reload left a run going with nothing on
    // screen and no way back to it. The server knows; ask.
    api
      .jobs()
      .then((jobs) => {
        const live = jobs.find((job) => !FINISHED.has(job.status));
        if (live) setJobId(live.job_id);
      })
      .catch(() => undefined);
  }, []);

  const start = async () => {
    setError(null);
    try {
      const job = await api.createCourse(prompt.trim());
      setJobId(job.job_id);
      setPrompt("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start that");
    }
  };

  return (
    <div className="page">
      <header className="bar">
        <span className="brand">LearnForge</span>
        <span className="spacer" />
        <span className="muted">{learner?.name || learner?.email}</span>
        <button className="link" onClick={signOut}>
          Sign out
        </button>
      </header>

      <section className="card ask">
        <h2>What do you want to learn?</h2>
        <div className="row">
          <input
            value={prompt}
            placeholder="e.g. Kubernetes operators, for someone who knows Go"
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && prompt.trim().length > 2 && start()}
          />
          <button onClick={start} disabled={prompt.trim().length < 3 || jobId !== null}>
            Build my course
          </button>
        </div>
        {error && <p className="error">{error}</p>}
      </section>

      {jobId && (
        <Generating
          jobId={jobId}
          onFinished={(courseId) => {
            setJobId(null);
            void load();
            if (courseId) navigate(`/read/${courseId}`);
          }}
          onCancel={() => setJobId(null)}
        />
      )}

      <h2 className="section">Your library</h2>
      {courses === null && <p className="muted">Loading…</p>}
      {courses?.length === 0 && (
        <p className="muted">Nothing yet. Ask for something above and it will appear here.</p>
      )}
      <div className="shelf">
        {courses?.map((course) => (
          <button
            key={course.course_id}
            className="book"
            onClick={() => navigate(`/read/${course.course_id}`)}
          >
            <span className="book-title">{course.title || "Untitled course"}</span>
            <span className="muted">
              {course.chapters} chapter{course.chapters === 1 ? "" : "s"}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
