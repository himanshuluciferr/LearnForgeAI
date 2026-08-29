import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, token } from "../api/client";

function respond(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
  } as Response);
}

afterEach(() => {
  token.clear();
  vi.restoreAllMocks();
});

describe("the api client", () => {
  it("attaches the token so no component has to remember to", async () => {
    token.set("abc123");
    const fetcher = respond([]);
    vi.stubGlobal("fetch", fetcher);

    await api.courses();

    const [, init] = fetcher.mock.calls[0];
    expect(init.headers.Authorization).toBe("Bearer abc123");
  });

  it("sends no authorization header when nobody is signed in", async () => {
    const fetcher = respond({ token: "t" });
    vi.stubGlobal("fetch", fetcher);

    await api.login("ada@example.com", "correct horse battery");

    expect(fetcher.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it("never sends a user id, because the server takes it from the token", async () => {
    token.set("abc123");
    const fetcher = respond({ job_id: "j" });
    vi.stubGlobal("fetch", fetcher);

    await api.createCourse("teach me rust");

    const [url, init] = fetcher.mock.calls[0];
    expect(url).not.toContain("user_id");
    expect(init.body).not.toContain("user_id");
  });

  it("raises what the api said rather than a generic failure", async () => {
    vi.stubGlobal("fetch", respond({ detail: "That email is already registered" }, 409));

    await expect(api.signup("ada@example.com", "correct horse battery", "Ada")).rejects.toThrow(
      "That email is already registered",
    );
  });

  it("still raises when the body is not json", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error("not json");
        },
      } as unknown as Response),
    );

    await expect(api.courses()).rejects.toBeInstanceOf(ApiError);
  });

  it("asks for a chapter quiz and the final quiz differently", async () => {
    const fetcher = respond({ questions: [] });
    vi.stubGlobal("fetch", fetcher);

    await api.quiz("c1", 2);
    await api.quiz("c1", null);

    expect(fetcher.mock.calls[0][0]).toBe("/quiz/c1?chapter=2");
    expect(fetcher.mock.calls[1][0]).toBe("/quiz/c1");
  });
});
