/** Watches a generation job.
 *
 *  Streams over SSE, and falls back to polling when the stream cannot be opened — a proxy
 *  that buffers or strips text/event-stream would otherwise leave the page silent forever.
 */

import { useEffect, useRef, useState } from "react";
import { api } from "./client";
import type { JobProgress } from "./types";

const POLL_MS = 2000;

export const SETTLED = new Set([
  "completed",
  "failed",
  "rejected",
  "needs-choice",
  "needs-confirmation",
]);

export function useJobProgress(jobId: string | null) {
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Read inside the poll loop, which would otherwise close over the first value forever.
  const settled = useRef(false);

  useEffect(() => {
    if (!jobId) return;
    settled.current = false;
    setProgress(null);
    setError(null);

    let source: EventSource | null = null;
    let timer: number | undefined;
    let stopped = false;

    const finish = (next: JobProgress) => {
      setProgress(next);
      if (SETTLED.has(next.status)) {
        settled.current = true;
        source?.close();
      }
    };

    const poll = async () => {
      while (!stopped && !settled.current) {
        try {
          finish(await api.jobProgress(jobId));
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "Lost contact with the server");
          return;
        }
        await new Promise((resume) => {
          timer = window.setTimeout(resume, POLL_MS);
        });
      }
    };

    const listen = async () => {
      try {
        const { ticket } = await api.streamTicket();
        if (stopped) return;
        source = new EventSource(`/courses/${jobId}/stream?ticket=${encodeURIComponent(ticket)}`);
        source.addEventListener("progress", (event) =>
          finish(JSON.parse((event as MessageEvent).data)),
        );
        source.addEventListener("done", (event) => finish(JSON.parse((event as MessageEvent).data)));
        source.addEventListener("error", () => {
          // Fires both for a server-sent error frame and for a dropped connection. Either way
          // the poll is the honest fallback.
          source?.close();
          if (!settled.current) void poll();
        });
      } catch {
        void poll();
      }
    };

    void listen();

    return () => {
      stopped = true;
      source?.close();
      window.clearTimeout(timer);
    };
  }, [jobId]);

  return { progress, error };
}
