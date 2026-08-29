/** Every call to the backend goes through here, so the token is attached in one place and a
 *  401 means one thing everywhere. */

import type {
  CourseDocument,
  CourseProgress,
  CourseSummary,
  JobProgress,
  Learner,
  MentorReply,
  QuizOut,
  QuizResult,
  Session,
} from "./types";

const TOKEN_KEY = "learnforge.token";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (value: string) => localStorage.setItem(TOKEN_KEY, value),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const held = token.get();
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(held ? { Authorization: `Bearer ${held}` } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    // The detail is what the API meant to say; anything else is a bug worth showing plainly.
    const detail = await response
      .json()
      .then((body) => body?.detail)
      .catch(() => null);
    throw new ApiError(response.status, detail ?? `Request failed (${response.status})`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  signup: (email: string, password: string, name: string) =>
    post<Session>("/auth/signup", { email, password, name }),

  login: (email: string, password: string) => post<Session>("/auth/login", { email, password }),

  me: () => request<Learner>("/auth/me"),

  streamTicket: () => post<{ ticket: string; expires_in: number }>("/auth/stream-ticket"),

  courses: () => request<CourseSummary[]>("/courses"),

  course: (courseId: string) => request<CourseDocument>(`/courses/${courseId}`),

  createCourse: (prompt: string) => post<{ job_id: string; status: string }>("/courses", { prompt }),

  confirm: (jobId: string, choice?: string) =>
    post<{ job_id: string; status: string }>(
      `/courses/${jobId}/confirm`,
      choice ? { choice } : undefined,
    ),

  jobProgress: (jobId: string) => request<JobProgress>(`/courses/${jobId}/progress`),

  jobs: () => request<JobProgress[]>("/jobs"),

  progress: (courseId: string) => request<CourseProgress>(`/progress/${courseId}`),

  markRead: (courseId: string, chapter: number) =>
    request<CourseProgress>(`/progress/${courseId}/chapters/${chapter}`, { method: "PUT" }),

  quiz: (courseId: string, chapter: number | null) =>
    request<QuizOut>(
      `/quiz/${courseId}${chapter === null ? "" : `?chapter=${chapter}`}`,
    ),

  submitQuiz: (courseId: string, chapter: number | null, answers: Record<number, number>) =>
    post<QuizResult>(
      `/quiz/${courseId}/answers${chapter === null ? "" : `?chapter=${chapter}`}`,
      { answers },
    ),

  ask: (courseId: string, question: string) =>
    post<MentorReply>(`/mentor/${courseId}`, { question }),
};
