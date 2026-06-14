import { fetchViewerJson } from "./constants";

export async function fetchNPCs() {
  return await fetchViewerJson<any[]>("viewer-data/npcs.json");
}
