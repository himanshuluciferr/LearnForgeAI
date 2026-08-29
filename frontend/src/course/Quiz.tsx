import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { QuizOut, QuizResult } from "../api/types";

interface Props {
  courseId: string;
  chapter: number | null;
  onClose: () => void;
}

export function Quiz({ courseId, chapter, onClose }: Props) {
  const [quiz, setQuiz] = useState<QuizOut | null>(null);
  const [chosen, setChosen] = useState<Record<number, number>>({});
  const [result, setResult] = useState<QuizResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.quiz(courseId, chapter).then(setQuiz).catch((caught) => setError(caught.message));
  }, [courseId, chapter]);

  const submit = async () => {
    try {
      // Marking happens on the server against the stored course, so the answer never has to
      // be here to be checked.
      setResult(await api.submitQuiz(courseId, chapter, chosen));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not mark that");
    }
  };

  return (
    <div className="backdrop" onClick={onClose}>
      <div className="window narrow" onClick={(event) => event.stopPropagation()}>
        <header className="bar">
          <h3>{quiz?.scope ?? "Quiz"}</h3>
          <span className="spacer" />
          <button className="link" onClick={onClose}>
            Close
          </button>
        </header>

        {error && <p className="error">{error}</p>}
        {!quiz && !error && <p className="muted">Loading…</p>}

        {quiz && !result && (
          <>
            {quiz.questions.map((question) => (
              <fieldset key={question.number} className="question">
                <legend>
                  {question.number}. {question.question}
                </legend>
                {question.options.map((option, index) => (
                  <label key={option} className="option">
                    <input
                      type="radio"
                      name={`q${question.number}`}
                      checked={chosen[question.number] === index}
                      onChange={() => setChosen({ ...chosen, [question.number]: index })}
                    />
                    {option}
                  </label>
                ))}
              </fieldset>
            ))}
            <button onClick={submit} disabled={Object.keys(chosen).length === 0}>
              Mark my answers
            </button>
          </>
        )}

        {result && quiz && (
          <div className="result">
            <h4>
              {result.correct} out of {result.total} — {result.percent}%
            </h4>
            {result.answers.map((answer) => {
              const question = quiz.questions[answer.number - 1];
              return (
                <div key={answer.number} className={answer.correct ? "right" : "wrong"}>
                  <p>
                    <strong>
                      {answer.number}. {question?.question}
                    </strong>
                  </p>
                  <p>
                    {answer.correct ? "Correct" : "The answer is"}:{" "}
                    {question?.options[answer.correct_index]}
                  </p>
                  <p className="muted">{answer.explanation}</p>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
