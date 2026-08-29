/** Mirrors the FastAPI response models. Kept in one file so a backend change breaks the
 *  build in one place rather than in whichever component happened to read the field. */

export type JobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "rejected"
  | "needs-choice"
  | "needs-confirmation";

export interface Session {
  token: string;
  user_id: string;
  email: string;
  name: string;
}

export interface Learner {
  user_id: string;
  email: string;
  name: string;
}

export interface JobProgress {
  job_id: string;
  status: JobStatus;
  step: string | null;
  percent: number;
  detail: string | null;
  options: string[];
  subject_name: string | null;
  subject_description: string | null;
  subject_sources: string[];
  error: string | null;
  course_id: string | null;
}

export interface CourseSummary {
  course_id: string;
  title: string;
  chapters: number;
  created_at: string;
}

export interface DiagramEdge {
  source: string;
  target: string;
  label: string;
}

export interface Diagram {
  kind: string;
  title: string;
  nodes: string[];
  edges: DiagramEdge[];
}

export interface Topic {
  number: number;
  label: string;
  title: string;
  what_it_is: string;
  why_it_matters: string;
  how_to_use: string;
  implementation: string;
  diagram: Diagram | null;
}

export interface Practice {
  kind: string;
  prompt: string;
  solution: string;
}

export interface Chapter {
  number: number;
  title: string;
  topics: Topic[];
  key_points: string[];
  exercises: string[];
  practice: Practice[];
  diagram: Diagram | null;
  has_quiz: boolean;
  /** Only set for courses generated before topics existed. */
  markdown: string;
}

export interface Project {
  level: string;
  title: string;
  summary: string;
  features: string[];
  folder_structure: string;
  milestones: string[];
  stretch_goals: string[];
}

export interface CourseDocument {
  course_id: string;
  title: string;
  summary: string;
  created_at: string;
  chapters: Chapter[];
  projects: Project[];
  has_final_quiz: boolean;
  markdown_url: string | null;
}

export interface ChapterProgress {
  number: number;
  title: string;
  read: boolean;
  best_quiz_percent: number | null;
}

export interface CourseProgress {
  course_id: string;
  title: string;
  chapters_read: number;
  chapters_total: number;
  percent: number;
  next_chapter: number | null;
  markdown_url: string | null;
  chapters: ChapterProgress[];
}

export interface QuizQuestion {
  number: number;
  question: string;
  options: string[];
}

export interface QuizOut {
  course_id: string;
  scope: string;
  chapter_number: number | null;
  questions: QuizQuestion[];
}

export interface MarkedAnswer {
  number: number;
  chosen_index: number | null;
  correct_index: number;
  correct: boolean;
  explanation: string;
}

export interface QuizResult {
  course_id: string;
  chapter_number: number | null;
  correct: number;
  total: number;
  percent: number;
  answers: MarkedAnswer[];
}

export interface MentorReply {
  course_id: string;
  question: string;
  answer: string;
  grounded: boolean;
  chapter_number: number | null;
  looked_up: boolean;
}
