import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Generating } from "../library/Generating";
import { api } from "../api/client";
import type { JobProgress } from "../api/types";

afterEach(() => vi.restoreAllMocks());

const job = (over: Partial<JobProgress> = {}): JobProgress => ({
  job_id: "j1",
  status: "running",
  step: "research",
  percent: 20,
  detail: null,
  options: [],
  subject_name: null,
  subject_description: null,
  subject_sources: [],
  error: null,
  course_id: null,
  ...over,
});

/** The hook opens a stream; jsdom has no EventSource, so it falls back to the poll and that
 *  is what these tests drive. */
function answering(...replies: JobProgress[]) {
  const queue = [...replies];
  vi.spyOn(api, "streamTicket").mockRejectedValue(new Error("no stream in jsdom"));
  vi.spyOn(api, "jobProgress").mockImplementation(async () =>
    queue.length > 1 ? queue.shift()! : queue[0],
  );
}

const confirmation = job({
  status: "needs-confirmation",
  percent: 10,
  subject_name: "NetworkPolicy",
  subject_description: "How pods may talk to each other.",
  subject_sources: ["https://kubernetes.io/docs/concepts/services-networking/network-policies/"],
});

describe("answering the subject gate", () => {
  it("goes back to watching once the learner says yes", async () => {
    // It used to freeze on the question it had just asked: needs-confirmation settles the
    // stream, and nothing reopened it when the job started running again.
    answering(confirmation, job({ percent: 30, step: "curriculum" }));
    vi.spyOn(api, "confirm").mockResolvedValue({ job_id: "j1", status: "running" });

    render(<Generating jobId="j1" onFinished={() => {}} onCancel={() => {}} />);
    await screen.findByText("NetworkPolicy");
    await userEvent.click(screen.getByRole("button", { name: /yes, that is it/i }));

    await waitFor(() => expect(screen.getByText(/30%/)).toBeInTheDocument());
  });

  it("shows what went wrong instead of throwing into nowhere", async () => {
    // `void api.confirm(...)` threw an unhandled 409 where the learner saw nothing at all.
    answering(confirmation);
    vi.spyOn(api, "confirm").mockRejectedValue(new Error("Job is running, so there is nothing to confirm"));

    render(<Generating jobId="j1" onFinished={() => {}} onCancel={() => {}} />);
    await screen.findByText("NetworkPolicy");
    await userEvent.click(screen.getByRole("button", { name: /yes, that is it/i }));

    await waitFor(() =>
      expect(screen.getByText(/nothing to confirm/i)).toBeInTheDocument(),
    );
  });

  it("answers once however many times the button is pressed", async () => {
    answering(confirmation);
    let release: () => void = () => {};
    const confirm = vi
      .spyOn(api, "confirm")
      .mockReturnValue(new Promise((resolve) => {
        release = () => resolve({ job_id: "j1", status: "running" });
      }));

    render(<Generating jobId="j1" onFinished={() => {}} onCancel={() => {}} />);
    const button = await screen.findByRole("button", { name: /yes, that is it/i });
    await userEvent.click(button);
    await userEvent.click(button);
    await userEvent.click(button);
    release();

    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("offers each option when the learner named several skills", async () => {
    answering(job({ status: "needs-choice", options: ["React", "Vue"], detail: "Which one?" }));
    const confirm = vi.spyOn(api, "confirm").mockResolvedValue({ job_id: "j1", status: "running" });

    render(<Generating jobId="j1" onFinished={() => {}} onCancel={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "Vue" }));

    expect(confirm).toHaveBeenCalledWith("j1", "Vue");
  });
});

describe("the rest of the run", () => {
  it("names the step rather than showing the raw identifier", async () => {
    answering(job({ step: "publisher", percent: 97 }));

    render(<Generating jobId="j1" onFinished={() => {}} onCancel={() => {}} />);

    expect(await screen.findByText(/binding the book/i)).toBeInTheDocument();
  });

  it("offers the finished course", async () => {
    answering(job({ status: "completed", percent: 100, course_id: "c1" }));
    const opened: (string | null)[] = [];

    render(
      <Generating jobId="j1" onFinished={(id) => opened.push(id)} onCancel={() => {}} />,
    );
    await userEvent.click(await screen.findByRole("button", { name: /open it/i }));

    expect(opened).toEqual(["c1"]);
  });

  it("says what failed rather than spinning forever", async () => {
    answering(job({ status: "failed", error: "the model refused" }));

    render(<Generating jobId="j1" onFinished={() => {}} onCancel={() => {}} />);

    expect(await screen.findByText(/the model refused/i)).toBeInTheDocument();
  });
});
