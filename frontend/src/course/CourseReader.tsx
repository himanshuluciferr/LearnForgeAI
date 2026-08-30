import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Chapter, CourseDocument, CourseProgress } from "../api/types";
import { ChapterWindow } from "./ChapterWindow";
import { CourseSkeleton } from "../ui/Skeleton";
import { Mentor } from "./Mentor";

export function CourseReader() {
  const { courseId = "" } = useParams();
  const navigate = useNavigate();
  const [course, setCourse] = useState<CourseDocument | null>(null);
  const [progress, setProgress] = useState<CourseProgress | null>(null);
  const [open, setOpen] = useState<Chapter | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // The whole book arrives in one response, so opening a chapter is a click and not a
    // request.
    api.course(courseId).then(setCourse).catch((caught) => setError(caught.message));
    api.progress(courseId).then(setProgress).catch(() => undefined);
  }, [courseId]);

  if (error) return <p className="error page">{error}</p>;
  if (!course) return <CourseSkeleton />;

  const readState = new Map(progress?.chapters.map((c) => [c.number, c]) ?? []);

  return (
    <div className="page">
      <header className="bar">
        <button className="link" onClick={() => navigate("/")}>
          ← Library
        </button>
        <span className="spacer" />
        {progress && (
          <span className="muted">
            {progress.chapters_read} of {progress.chapters_total} read
          </span>
        )}
      </header>

      <h1>{course.title}</h1>
      <p className="muted">{course.summary}</p>

      <div className="contents">
        {course.chapters.map((chapter) => {
          const state = readState.get(chapter.number);
          return (
            <button key={chapter.number} className="chapter-row" onClick={() => setOpen(chapter)}>
              <span className="chapter-number">{chapter.number}</span>
              <span className="chapter-name">{chapter.title}</span>
              {state?.best_quiz_percent != null && (
                <span className="pill">{state.best_quiz_percent}%</span>
              )}
              {state?.read && <span className="tick">✓</span>}
            </button>
          );
        })}
      </div>

      {course.projects.length > 0 && (
        <>
          <h2 className="section">Projects</h2>
          {course.projects.map((project) => (
            <section key={project.title} className="card">
              <h3>
                {project.title} <span className="pill">{project.level}</span>
              </h3>
              <p>{project.summary}</p>
              <ul>
                {project.features.map((feature) => (
                  <li key={feature}>{feature}</li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}

      <Mentor courseId={courseId} />

      {open && (
        <ChapterWindow
          courseId={courseId}
          chapter={open}
          onClose={() => setOpen(null)}
          onRead={setProgress}
        />
      )}
    </div>
  );
}
