import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Library } from "../library/Library";
import { AuthProvider } from "../auth/AuthContext";
import { api, token } from "../api/client";
import type { CourseSummary, JobProgress, JobStatus } from "../api/types";

const job = (status: JobStatus, over: Partial<JobProgress> = {}): JobProgress => ({
  job_id: "j1",
  status,
  step: "chapter",
  percent: 60,
  detail: null,
  options: [],
  subject_name: null,
  subject_description: null,
  subject_sources: [],
  error: null,
  course_id: null,
  ...over,
});

const course: CourseSummary = {
  course_id: "c1",
  title: "Kubernetes Operators",
  chapters: 5,
  created_at: "2026-01-01T00:00:00Z",
};

function show() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Library />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  token.set("a-token");
  vi.spyOn(api, "me").mockResolvedValue({ user_id: "u1", email: "ada@example.com", name: "Ada" });
  // jsdom has no EventSource, so the hook falls back to the poll.
  vi.spyOn(api, "streamTicket").mockRejectedValue(new Error("no stream in jsdom"));
});

afterEach(() => {
  token.clear();
  vi.restoreAllMocks();
});

describe("coming back to the library", () => {
  it("picks up a run that was still going", async () => {
    // The job id lived only in component state, so a reload left the run going with nothing
    // on screen and no way back to it.
    vi.spyOn(api, "courses").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockResolvedValue([job("running")]);
    vi.spyOn(api, "jobProgress").mockResolvedValue(job("running"));

    show();

    expect(await screen.findByText(/writing the chapters/i)).toBeInTheDocument();
  });

  it("comes back to a run that is waiting on an answer", async () => {
    // A gate is settled as far as the stream goes, but the learner still has to answer it.
    vi.spyOn(api, "courses").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockResolvedValue([
      job("needs-confirmation", { subject_name: "NetworkPolicy" }),
    ]);
    vi.spyOn(api, "jobProgress").mockResolvedValue(
      job("needs-confirmation", { subject_name: "NetworkPolicy" }),
    );

    show();

    expect(await screen.findByText("NetworkPolicy")).toBeInTheDocument();
  });

  it("does not reopen a run that already finished", async () => {
    vi.spyOn(api, "courses").mockResolvedValue([course]);
    vi.spyOn(api, "jobs").mockResolvedValue([job("completed", { percent: 100 })]);
    const progress = vi.spyOn(api, "jobProgress");

    show();

    await screen.findByText("Kubernetes Operators");
    expect(progress).not.toHaveBeenCalled();
  });

  it("does not reopen a run that failed", async () => {
    vi.spyOn(api, "courses").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockResolvedValue([job("failed", { error: "the server restarted" })]);
    const progress = vi.spyOn(api, "jobProgress");

    show();

    await waitFor(() => expect(screen.getByText(/nothing yet/i)).toBeInTheDocument());
    expect(progress).not.toHaveBeenCalled();
  });

  it("still shows the library when the job listing fails", async () => {
    vi.spyOn(api, "courses").mockResolvedValue([course]);
    vi.spyOn(api, "jobs").mockRejectedValue(new Error("jobs are down"));

    show();

    expect(await screen.findByText("Kubernetes Operators")).toBeInTheDocument();
  });
});

describe("while things are on their way", () => {
  it("shows the shape of the library rather than a blank word", async () => {
    // A course is a few hundred kilobytes read from another continent; this is on screen for
    // seconds, not a flicker.
    vi.spyOn(api, "jobs").mockResolvedValue([]);
    vi.spyOn(api, "courses").mockReturnValue(new Promise(() => {}));

    show();

    await waitFor(() =>
      expect(screen.getByLabelText(/loading your courses/i)).toBeInTheDocument(),
    );
    expect(document.querySelectorAll(".book.skeleton").length).toBeGreaterThan(0);
  });

  it("stops showing it once the courses arrive", async () => {
    vi.spyOn(api, "jobs").mockResolvedValue([]);
    vi.spyOn(api, "courses").mockResolvedValue([course]);

    show();

    await screen.findByText("Kubernetes Operators");
    expect(screen.queryByLabelText(/loading your courses/i)).toBeNull();
  });
});
