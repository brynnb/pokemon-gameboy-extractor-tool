import { fetchViewerJson } from "./constants";

export async function fetchWarps() {
  return await fetchViewerJson<any[]>("viewer-data/warps.json");
}
