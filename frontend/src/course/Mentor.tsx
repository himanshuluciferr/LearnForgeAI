import { useState, type FormEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "../api/client";
import type { MentorReply } from "../api/types";

interface Turn {
  question: string;
  reply: MentorReply | null;
}

export function Mentor({ courseId }: { courseId: string }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);

  const ask = async (event: FormEvent) => {
    event.preventDefault();
    const asked = question.trim();
    if (!asked) return;

    setQuestion("");
    setBusy(true);
    setTurns((before) => [...before, { question: asked, reply: null }]);
    try {
      const reply = await api.ask(courseId, asked);
      setTurns((before) => before.map((turn, i) => (i === before.length - 1 ? { ...turn, reply } : turn)));
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "The mentor is unavailable";
      setTurns((before) =>
        before.map((turn, i) =>
          i === before.length - 1
            ? {
                ...turn,
                reply: {
                  course_id: courseId,
                  question: asked,
                  answer: message,
                  grounded: false,
                  chapter_number: null,
                  looked_up: false,
                },
              }
            : turn,
        ),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card mentor">
      <h2>Ask the mentor</h2>
      <p className="muted small">
        Answers come from this course. When it does not cover something, it says so rather than
        guessing.
      </p>

      <div className="chat">
        {turns.map((turn, index) => (
          <div key={`${turn.question}-${index}`}>
            <p className="asked">{turn.question}</p>
            {turn.reply ? (
              <div className="answered">
                <Markdown remarkPlugins={[remarkGfm]}>{turn.reply.answer}</Markdown>
                <p className="muted small">
                  {turn.reply.chapter_number != null && `From chapter ${turn.reply.chapter_number}`}
                  {turn.reply.looked_up && "Looked this one up"}
                </p>
              </div>
            ) : (
              <p className="muted">Thinking…</p>
            )}
          </div>
        ))}
      </div>

      <form className="row" onSubmit={ask}>
        <input
          value={question}
          placeholder="Ask anything about this course"
          onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" disabled={busy || !question.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}
