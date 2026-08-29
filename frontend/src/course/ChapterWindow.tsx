import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";
import type { Chapter, CourseProgress, Diagram, Topic } from "../api/types";
import { Quiz } from "./Quiz";

interface Props {
  courseId: string;
  chapter: Chapter;
  onClose: () => void;
  onRead: (progress: CourseProgress) => void;
}

export function ChapterWindow({ courseId, chapter, onClose, onRead }: Props) {
  const [quizOpen, setQuizOpen] = useState(false);
  const [read, setRead] = useState(false);

  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);

  const markRead = async () => {
    try {
      onRead(await api.markRead(courseId, chapter.number));
      setRead(true);
    } catch {
      // Losing a tick is not worth interrupting the reader over.
    }
  };

  return (
    <div className="backdrop" onClick={onClose}>
      <article className="window" onClick={(event) => event.stopPropagation()}>
        {/* The actions live here rather than after the text: a chapter runs to tens of
            screens, and a button at the end of it is one nobody reaches. */}
        <header className="bar">
          <h2>
            {chapter.number}. {chapter.title}
          </h2>
          <span className="spacer" />
          <button onClick={markRead} disabled={read}>
            {read ? "Read \u2713" : "Mark as read"}
          </button>
          {chapter.has_quiz && <button onClick={() => setQuizOpen(true)}>Quiz</button>}
          <button className="link" onClick={onClose}>
            Close
          </button>
        </header>

        <div className="reading">
          {/* Older courses carry only rendered markdown; newer ones are built from blocks. */}
          {chapter.markdown ? (
            <Markdown remarkPlugins={[remarkGfm]}>{chapter.markdown}</Markdown>
          ) : (
            chapter.topics.map((topic) => <TopicBlock key={topic.label} topic={topic} />)
          )}

          {chapter.key_points.length > 0 && (
            <section className="callout">
              <h4>Worth remembering</h4>
              <ul>
                {chapter.key_points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </section>
          )}

          {chapter.exercises.length > 0 && (
            <section>
              <h4>Try it yourself</h4>
              <ol>
                {chapter.exercises.map((exercise) => (
                  <li key={exercise}>{exercise}</li>
                ))}
              </ol>
            </section>
          )}

          {chapter.practice.map((item) => (
            <details key={item.prompt} className="practice">
              <summary>
                <span className="pill">{item.kind}</span> {item.prompt}
              </summary>
              <Markdown remarkPlugins={[remarkGfm]}>{item.solution}</Markdown>
            </details>
          ))}
        </div>

        {quizOpen && (
          <Quiz courseId={courseId} chapter={chapter.number} onClose={() => setQuizOpen(false)} />
        )}
      </article>
    </div>
  );
}

function TopicBlock({ topic }: { topic: Topic }) {
  return (
    <section className="topic">
      <h3>
        {topic.label} {topic.title}
      </h3>
      <Block label="What it is" body={topic.what_it_is} />
      <Block label="Why it matters" body={topic.why_it_matters} />
      <Block label="How to use it" body={topic.how_to_use} />
      {topic.implementation && (
        <div className="block">
          <h4>In practice</h4>
          <Markdown remarkPlugins={[remarkGfm]}>{topic.implementation}</Markdown>
        </div>
      )}
      {topic.diagram && <DiagramView diagram={topic.diagram} />}
    </section>
  );
}

function Block({ label, body }: { label: string; body: string }) {
  if (!body) return null;
  return (
    <div className="block">
      <h4>{label}</h4>
      <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
    </div>
  );
}

function DiagramView({ diagram }: { diagram: Diagram }) {
  return (
    <figure className="diagram">
      <figcaption>{diagram.title}</figcaption>
      <ul>
        {diagram.edges.map((edge) => (
          <li key={`${edge.source}-${edge.target}-${edge.label}`}>
            <span className="node">{edge.source}</span>
            <span className="arrow">{edge.label ? `— ${edge.label} →` : "→"}</span>
            <span className="node">{edge.target}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}
