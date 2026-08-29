import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Quiz } from "../course/Quiz";
import { ChapterWindow } from "../course/ChapterWindow";
import { api } from "../api/client";
import type { Chapter } from "../api/types";

afterEach(() => vi.restoreAllMocks());

const QUIZ = {
  course_id: "c1",
  scope: "Chapter 1",
  chapter_number: 1,
  questions: [
    { number: 1, question: "Which one reconciles?", options: ["alpha", "bravo", "charlie"] },
  ],
};

describe("taking a quiz", () => {
  it("shows the options without saying which is right", async () => {
    vi.spyOn(api, "quiz").mockResolvedValue(QUIZ);

    render(<Quiz courseId="c1" chapter={1} onClose={() => {}} />);

    await screen.findByText(/which one reconciles/i);
    // Nothing in the rendered page should be able to give the answer away, because the
    // server never sent it.
    expect(document.body.textContent).not.toContain("correct_index");
  });

  it("takes the score from the server rather than working it out here", async () => {
    vi.spyOn(api, "quiz").mockResolvedValue(QUIZ);
    const submit = vi.spyOn(api, "submitQuiz").mockResolvedValue({
      course_id: "c1",
      chapter_number: 1,
      correct: 1,
      total: 1,
      percent: 100,
      answers: [
        { number: 1, chosen_index: 0, correct_index: 0, correct: true, explanation: "because" },
      ],
    });

    render(<Quiz courseId="c1" chapter={1} onClose={() => {}} />);
    await screen.findByText(/which one reconciles/i);
    await userEvent.click(screen.getByLabelText("alpha"));
    await userEvent.click(screen.getByRole("button", { name: /mark my answers/i }));

    await waitFor(() => expect(screen.getByText(/1 out of 1/)).toBeInTheDocument());
    expect(submit).toHaveBeenCalledWith("c1", 1, { 1: 0 });
  });

  it("cannot be marked before anything is chosen", async () => {
    vi.spyOn(api, "quiz").mockResolvedValue(QUIZ);

    render(<Quiz courseId="c1" chapter={1} onClose={() => {}} />);

    await screen.findByText(/which one reconciles/i);
    expect(screen.getByRole("button", { name: /mark my answers/i })).toBeDisabled();
  });
});

const chapter = (over: Partial<Chapter> = {}): Chapter => ({
  number: 1,
  title: "Controllers",
  topics: [],
  key_points: [],
  exercises: [],
  practice: [],
  diagram: null,
  has_quiz: false,
  markdown: "",
  ...over,
});

describe("reading a chapter", () => {
  it("renders each topic as its named blocks", () => {
    render(
      <ChapterWindow
        courseId="c1"
        onClose={() => {}}
        onRead={() => {}}
        chapter={chapter({
          topics: [
            {
              number: 1,
              label: "1.1",
              title: "Reconcile loops",
              what_it_is: "A loop.",
              why_it_matters: "It converges.",
              how_to_use: "Return a result.",
              implementation: "",
              diagram: null,
            },
          ],
        })}
      />,
    );

    expect(screen.getByText("What it is")).toBeInTheDocument();
    expect(screen.getByText("Why it matters")).toBeInTheDocument();
    expect(screen.getByText(/it converges/i)).toBeInTheDocument();
  });

  it("falls back to markdown for a course written before topics existed", () => {
    render(
      <ChapterWindow
        courseId="c1"
        onClose={() => {}}
        onRead={() => {}}
        chapter={chapter({ markdown: "# Old chapter\n\nStill readable." })}
      />,
    );

    expect(screen.getByText(/still readable/i)).toBeInTheDocument();
  });

  it("offers the quiz only where there is one", () => {
    const { rerender } = render(
      <ChapterWindow courseId="c1" onClose={() => {}} onRead={() => {}} chapter={chapter()} />,
    );
    expect(screen.queryByRole("button", { name: /^quiz$/i })).toBeNull();

    rerender(
      <ChapterWindow
        courseId="c1"
        onClose={() => {}}
        onRead={() => {}}
        chapter={chapter({ has_quiz: true })}
      />,
    );
    expect(screen.getByRole("button", { name: /^quiz$/i })).toBeInTheDocument();
  });

  it("marks the chapter that is actually open", async () => {
    // It once recorded a different chapter than the one on screen, which is the kind of bug
    // that only shows up when the number is checked rather than the tick.
    const mark = vi.spyOn(api, "markRead").mockResolvedValue({
      course_id: "c1",
      title: "t",
      chapters_read: 1,
      chapters_total: 3,
      percent: 33,
      next_chapter: 1,
      markdown_url: null,
      chapters: [],
    });

    render(
      <ChapterWindow
        courseId="c1"
        onClose={() => {}}
        onRead={() => {}}
        chapter={chapter({ number: 7, title: "Seventh" })}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /mark as read/i }));

    expect(mark).toHaveBeenCalledWith("c1", 7);
  });

  it("keeps its actions reachable without scrolling the whole chapter", () => {
    // They used to sit after the text, which on a thirty-screen chapter is nowhere.
    render(
      <ChapterWindow
        courseId="c1"
        onClose={() => {}}
        onRead={() => {}}
        chapter={chapter({ has_quiz: true })}
      />,
    );

    const header = document.querySelector("header.bar")!;
    expect(header.querySelector("button")).toBeTruthy();
    expect(header.textContent).toMatch(/mark as read/i);
    expect(header.textContent).toMatch(/quiz/i);
  });
});
