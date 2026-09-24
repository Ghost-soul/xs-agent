import { describe, expect, it } from "vitest";

import { readRoute } from "./routeState";

describe("route state", () => {
  it("reads canonical author routes", () => {
    const location = new URL("http://localhost/projects/project-1/read?chapter=chapter-2");
    expect(readRoute(location as unknown as Location)).toMatchObject({
      projectId: "project-1",
      view: "reader",
      chapterId: "chapter-2",
    });
  });

  it("keeps legacy query links compatible", () => {
    const location = new URL("http://localhost/?project=project-1&view=versions");
    expect(readRoute(location as unknown as Location)).toMatchObject({
      projectId: "project-1",
      view: "versions",
    });
  });

  it("maps retired generation links to the management home", () => {
    const story = new URL("http://localhost/projects/p/story?section=continuity");
    const review = new URL("http://localhost/projects/p/review?session=s1&run=r1");
    expect(readRoute(story as unknown as Location).view).toBe("longform");
    expect(readRoute(review as unknown as Location)).toMatchObject({
      view: "home",
    });
  });
});
