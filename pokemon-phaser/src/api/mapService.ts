import { fetchViewerJson } from "./constants";

export const fetchMapInfo = async (mapId: number): Promise<any> => {
  return await fetchViewerJson<any>(`viewer-data/map-info/${mapId}.json`);
};

export const fetchOverworldMaps = async (): Promise<any[]> => {
  return await fetchViewerJson<any[]>("viewer-data/overworld-maps.json");
};
