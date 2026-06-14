import { fetchViewerJson, viewerUrl } from "./constants";

export interface TileImageCacheEntry {
  key: string;
  path: string;
}

export const getTileImageUrl = (tileId: number): string => {
  const imageIndex = Math.max(0, tileId - 1);
  return viewerUrl(`viewer-assets/tile_images/tile_${imageIndex}.png`);
};

export const fetchTileImages = async (): Promise<any[]> => {
  return await fetchViewerJson<any[]>("viewer-data/tile-images.json");
};

export const fetchTiles = async (mapId: number): Promise<any[]> => {
  return await fetchViewerJson<any[]>(`viewer-data/tiles/${mapId}.json`);
};
