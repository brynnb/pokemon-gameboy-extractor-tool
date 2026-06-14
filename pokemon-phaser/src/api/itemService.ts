import { fetchViewerJson } from "./constants";

export const fetchItems = async (): Promise<any[]> => {
  return await fetchViewerJson<any[]>("viewer-data/items.json");
};
