import { expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { saveDraftWithRetry, saveThenExport } from "./exportWorkflow";

it("manual save retries with the latest revision after a conflict", async () => {
  const save = vi
    .fn()
    .mockRejectedValueOnce(new ApiError(409, "conflict"))
    .mockResolvedValueOnce({ revision: 6 });
  const api = {
    save,
    getDraft: vi.fn(async () => ({ revision: 5 })),
  };

  const revision = await saveDraftWithRetry(api, { id: "draft", revision: 4 }, []);

  expect(revision).toBe(6);
  expect(api.getDraft).toHaveBeenCalledWith("draft");
  expect(save).toHaveBeenNthCalledWith(1, "draft", 4, []);
  expect(save).toHaveBeenNthCalledWith(2, "draft", 5, []);
});

it("saves unsaved edits before export and retries a revision conflict", async () => {
  const calls: string[] = [];
  const api = {
    save: vi.fn(async (_id: string, revision: number) => {
      calls.push(`save:${revision}`);
      if (revision === 2) throw new ApiError(409, "conflict");
      return { revision: 4 };
    }),
    getDraft: vi.fn(async () => ({ revision: 3 })),
    export: vi.fn(async () => {
      calls.push("export");
      return { version_id: "v1" };
    }),
  };
  const result = await saveThenExport(api, { id: "d", revision: 2 }, [
    {
      id: "x",
      seq: 1,
      type: "cover_region",
      page_index: 0,
      bbox: [1, 1, 2, 2],
    },
  ]);
  expect(calls).toEqual(["save:2", "save:3", "export"]);
  expect(result).toEqual({ revision: 4, version_id: "v1" });
});
