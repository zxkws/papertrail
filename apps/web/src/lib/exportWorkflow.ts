import { ApiError } from "./api";
import type { Operation } from "../types";

interface ExportApi {
  save: (
    id: string,
    revision: number,
    ops: Operation[],
  ) => Promise<{ revision: number }>;
  getDraft: (id: string) => Promise<{ revision: number }>;
  export: (id: string) => Promise<{ version_id: string }>;
}

type DraftApi = Pick<ExportApi, "save" | "getDraft">;

export async function saveDraftWithRetry(
  client: DraftApi,
  draft: { id: string; revision: number },
  ops: Operation[],
): Promise<number> {
  try {
    return (await client.save(draft.id, draft.revision, ops)).revision;
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 409) throw error;
    const latestRevision = (await client.getDraft(draft.id)).revision;
    return (await client.save(draft.id, latestRevision, ops)).revision;
  }
}

export async function saveThenExport(
  client: ExportApi,
  draft: { id: string; revision: number },
  ops: Operation[],
) {
  const revision = await saveDraftWithRetry(client, draft, ops);
  const result = await client.export(draft.id);
  return { revision, version_id: result.version_id };
}
